"""Generation quota lifecycle for sandboxed image and video commands."""

from __future__ import annotations

import hashlib
import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, cast

from deerflow.tools.types import Runtime

_IMAGE_GENERATION_COMMAND_MARKERS = (
    "/mnt/skills/public/image-generation/scripts/generate.py",
    "image-generation/scripts/generate.py",
    "/mnt/skills/public/image-editing/scripts/edit.py",
    "image-editing/scripts/edit.py",
)
_VIDEO_GENERATION_COMMAND_MARKERS = (
    "/mnt/skills/public/video-generation/scripts/generate.py",
    "video-generation/scripts/generate.py",
)

_VIDEO_FAILURE_STATUSES = {"failed", "rejected", "cancelled", "canceled", "expired"}
_VIDEO_SUCCESS_STATUSES = {"succeeded", "provider_succeeded"}
_BILLING_REQUIRED_FLAGS = ("--model", "--resolution", "--duration", "--output-file")

SidecarLoader = Callable[[str | None], dict[str, object] | None]


class GenerationQuotaBridge(Protocol):
    async def reserve_image_generations(self, count: int) -> object: ...

    async def release_image_generations(self, reservation: object) -> None: ...

    async def reserve_video_generation(
        self,
        *,
        model: str | None,
        resolution: str | None,
        duration_seconds: int | None,
        idempotency_key: str,
        provider: str | None,
        output_file: str | None,
        operation: str = "generation",
    ) -> object: ...

    async def release_video_points(
        self,
        reservation: object | str,
        *,
        usage_period_id: str | None = None,
        reason: str | None = None,
    ) -> None: ...

    async def settle_video_points(
        self,
        reservation: object | str,
        *,
        usage_period_id: str | None = None,
        provider_task_id: object | None = None,
        billable_duration_seconds: object | None = None,
    ) -> None: ...

    async def mark_video_points_pending(
        self,
        reservation: object | str,
        *,
        usage_period_id: str | None = None,
        provider_task_id: object | None = None,
        reason: str,
    ) -> None: ...


@dataclass(frozen=True)
class VideoGenerationInvocation:
    """Statically parsed arguments for one video-generation script call."""

    operation: str
    model: str | None = None
    resolution: str | None = None
    duration_seconds: int | None = None
    output_file: str | None = None
    provider: str | None = None
    upscale_video: str | None = None


@dataclass(frozen=True)
class GenerationCommand:
    """Generation-related facts parsed from one Bash command."""

    command: str
    image_generation_count: int
    video_invocations: tuple[VideoGenerationInvocation, ...]

    @property
    def video_generation_count(self) -> int:
        return sum(item.operation == "generate" for item in self.video_invocations)

    @property
    def generate_invocations(self) -> tuple[VideoGenerationInvocation, ...]:
        return tuple(item for item in self.video_invocations if item.operation == "generate")


@dataclass(frozen=True)
class PreparedGenerationCommand:
    """A command whose generation quota has been checked before dispatch."""

    command: str
    analysis: GenerationCommand
    image_reservation: object | None
    video_reservation: object | None


def _is_generation_script(token: str, markers: tuple[str, ...]) -> bool:
    normalized = token.strip()
    return any(normalized == marker or normalized.endswith(f"/{marker}") for marker in markers)


def _option_value(args: list[str], name: str) -> str | None:
    """Read a simple CLI option without evaluating shell expressions."""
    prefix = f"{name}="
    for index, arg in enumerate(args):
        if arg == name:
            if index + 1 < len(args):
                return args[index + 1]
            return None
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _has_option(args: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in args)


def _normalize_bash_lines(command: str) -> str:
    """Join bash ``\\`` line continuations (LF/CRLF) so argument lines stay attached to their command."""
    command = command.replace("\r\n", "\n")
    # 行尾反斜杠连续段为奇数才是续行；偶数个是转义反斜杠，换行为命令结束
    return re.sub(r"((?<!\\)(?:\\\\)*)\\\n", r"\1 ", command)


def _video_invocation_from_segment(segment: list[str]) -> VideoGenerationInvocation | None:
    """Parse a single shell segment that invokes the video script."""
    if not segment:
        return None
    index = 0
    while index < len(segment) and "=" in segment[index] and not segment[index].startswith("="):
        index += 1
    while index < len(segment) and segment[index] in {"command", "builtin"}:
        index += 1
    if index >= len(segment):
        return None
    executable = posixpath.basename(segment[index])
    args = segment[index + 1 :]
    script_index: int | None = None
    if executable in {"python", "python3", "uv"}:
        script_index = next(
            (item for item, value in enumerate(args) if _is_generation_script(value, _VIDEO_GENERATION_COMMAND_MARKERS)),
            None,
        )
    elif _is_generation_script(segment[index], _VIDEO_GENERATION_COMMAND_MARKERS):
        script_index = -1
    if script_index is None:
        return None
    script_args = args[script_index + 1 :] if script_index >= 0 else args
    operation = "cancel" if _has_option(script_args, "--cancel") else "query" if _has_option(script_args, "--query") else "generate"
    duration_value = _option_value(script_args, "--duration")
    duration: int | None = None
    if duration_value is not None and not duration_value.startswith(("$", "`")):
        try:
            duration = int(duration_value)
        except ValueError:
            pass
    return VideoGenerationInvocation(
        operation=operation,
        model=_option_value(script_args, "--model"),
        resolution=_option_value(script_args, "--resolution"),
        duration_seconds=duration,
        output_file=_option_value(script_args, "--output-file"),
        provider=_option_value(script_args, "--provider"),
        upscale_video=_option_value(script_args, "--upscale-video"),
    )


def parse_video_invocations(command: str) -> list[VideoGenerationInvocation]:
    """Enumerate statically visible video script calls in *command*."""
    try:
        lexer = shlex.shlex(_normalize_bash_lines(command).replace("\n", ";"), posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return []

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)

    invocations: list[VideoGenerationInvocation] = []
    for segment in segments:
        if not segment:
            continue
        index = 0
        while index < len(segment) and "=" in segment[index] and not segment[index].startswith("="):
            index += 1
        if index >= len(segment):
            continue
        executable = posixpath.basename(segment[index])
        args = segment[index + 1 :]
        if executable == "env":
            invocations.extend(parse_video_invocations(" ".join(args)))
            continue
        if executable in {"bash", "sh", "zsh"} and "-c" in args:
            shell_index = args.index("-c")
            if shell_index + 1 < len(args):
                invocations.extend(parse_video_invocations(args[shell_index + 1]))
            continue
        parsed = _video_invocation_from_segment(segment)
        if parsed is not None:
            invocations.append(parsed)
    return invocations


def _generation_invocation_count(command: str, markers: tuple[str, ...]) -> int:
    """Count billable generation-script invocations matching *markers*."""
    try:
        lexer = shlex.shlex(_normalize_bash_lines(command).replace("\n", ";"), posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return 0

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)

    count = 0
    for segment in segments:
        if not segment:
            continue
        index = 0
        while index < len(segment) and "=" in segment[index] and not segment[index].startswith("="):
            index += 1
        if index >= len(segment):
            continue
        executable = posixpath.basename(segment[index])
        args = segment[index + 1 :]
        if executable == "env":
            count += _generation_invocation_count(" ".join(args), markers)
        elif executable in {"bash", "sh", "zsh"} and "-c" in args:
            shell_index = args.index("-c")
            if shell_index + 1 < len(args):
                count += _generation_invocation_count(args[shell_index + 1], markers)
        elif executable in {"python", "python3", "uv"}:
            if any(_is_generation_script(arg, markers) for arg in args):
                count += 1
        elif _is_generation_script(segment[index], markers):
            count += 1
    return count


def count_image_invocations(command: str) -> int:
    """Count billable image-generation and image-editing script invocations."""
    return _generation_invocation_count(command, _IMAGE_GENERATION_COMMAND_MARKERS)


def count_video_invocations(command: str) -> int:
    """Count billable video-generation script invocations."""
    return sum(item.operation == "generate" for item in parse_video_invocations(command))


def has_unparsed_video_intent(command: str, invocations: list[VideoGenerationInvocation]) -> bool:
    """Detect likely dynamic/wrapped video execution that static parsing missed."""
    if invocations or not any(marker in command for marker in _VIDEO_GENERATION_COMMAND_MARKERS):
        return False
    if re.search(r"\b(?:python(?:3)?|uv|bash|sh|zsh|timeout|xargs|sudo|env)\b", command):
        return True
    return bool(re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", command))


def _is_points_video_reservation(reservation: object | None) -> bool:
    return reservation is not None and hasattr(reservation, "reserved_minor_units") and hasattr(reservation, "usage_period_id")


def _static_video_value(value: str | None) -> bool:
    """Only use literal CLI values for billing; shell expansion is ambiguous."""
    return bool(value and not any(token in value for token in ("$", "`", "$(", ";", "|", "&")))


class GenerationQuotaLifecycle:
    """Coordinates image and video quota around one foreground Bash command."""

    def __init__(self, runtime: Runtime, sidecar_loader: SidecarLoader):
        self._runtime = runtime
        self._sidecar_loader = sidecar_loader

    def analyze(self, command: str) -> GenerationCommand:
        return GenerationCommand(
            command=command,
            image_generation_count=count_image_invocations(command),
            video_invocations=tuple(parse_video_invocations(command)),
        )

    def sync_error(self, analysis: GenerationCommand) -> str | None:
        """Reject synchronous Bash when generation requires the async bridge."""
        context = self._runtime.context or {}
        if context.get("__quota_enforcement_required") and has_unparsed_video_intent(
            analysis.command,
            list(analysis.video_invocations),
        ):
            return "Error: 无法安全识别视频生成命令及计费参数，请使用包含明确 model、resolution、duration 和 output-file 的单条命令。"
        if analysis.image_generation_count <= 0 and analysis.video_generation_count <= 0:
            return None
        quota_bridge = context.get("__quota_runtime_bridge")
        if context.get("__quota_enforcement_required") and quota_bridge is None:
            return "Error: Generation quota service is unavailable."
        if quota_bridge is not None:
            return "Error: Generation quota checks require asynchronous foreground bash execution."
        return None

    async def prepare(self, command: str) -> tuple[PreparedGenerationCommand | None, str | None]:
        """Parse and reserve quota before the sandbox command is dispatched."""
        analysis = self.analyze(command)
        context = self._runtime.context or {}
        if context.get("__quota_enforcement_required") and has_unparsed_video_intent(
            command,
            list(analysis.video_invocations),
        ):
            return None, "Error: 无法安全识别视频生成命令及计费参数，请使用包含明确 model、resolution、duration 和 output-file 的单条命令。"

        image_reservation, video_reservation, quota_error = await self._reserve(analysis)
        if quota_error:
            return None, quota_error

        prepared_command = command
        if video_reservation is not None and _is_points_video_reservation(video_reservation) and len(analysis.generate_invocations) == 1:
            prepared_command = self._with_video_billing_env(video_reservation, command)
        return (
            PreparedGenerationCommand(
                command=prepared_command,
                analysis=analysis,
                image_reservation=image_reservation,
                video_reservation=video_reservation,
            ),
            None,
        )

    async def finish(self, prepared: PreparedGenerationCommand, dispatched: bool) -> str | None:
        """Release, settle, or reconcile quota after the sandbox command returns."""
        if (prepared.image_reservation is not None or prepared.video_reservation is not None) and not dispatched:
            return await self._release(prepared.image_reservation, prepared.video_reservation)

        generate_invocations = prepared.analysis.generate_invocations
        if prepared.video_reservation is not None and _is_points_video_reservation(prepared.video_reservation) and len(generate_invocations) == 1:
            return await self._finalize_video_points(
                prepared.video_reservation,
                self._resolve_video_invocation(generate_invocations[0]),
                dispatched,
            )
        if not generate_invocations and prepared.analysis.video_invocations:
            return await self._reconcile_video_sidecar(prepared.analysis.video_invocations[0])
        return None

    def _bridge(self) -> GenerationQuotaBridge | None:
        bridge = (self._runtime.context or {}).get("__quota_runtime_bridge")
        return cast(GenerationQuotaBridge, bridge) if bridge is not None else None

    async def _reserve(self, analysis: GenerationCommand) -> tuple[object | None, object | None, str | None]:
        if analysis.image_generation_count <= 0 and analysis.video_generation_count <= 0:
            return None, None, None

        context = self._runtime.context or {}
        quota_bridge = self._bridge()
        if context.get("__quota_enforcement_required") and quota_bridge is None:
            return None, None, "Error: Generation quota service is unavailable."
        if quota_bridge is None:
            return None, None, None

        image_reservation: object | None = None
        if analysis.image_generation_count > 0:
            try:
                result = await quota_bridge.reserve_image_generations(analysis.image_generation_count)
            except Exception as exc:  # noqa: BLE001 - tool boundary converts failures to text
                return None, None, f"Error: Failed to check image generation quota: {exc}"
            if not isinstance(result, dict) or not result.get("allowed", False):
                message = result.get("message") if isinstance(result, dict) else None
                return None, None, f"Error: {message or '生图额度检查返回无效结果'}"
            image_reservation = result.get("reservation")

        if analysis.video_generation_count <= 0:
            return image_reservation, None, None

        invocations = [self._resolve_video_invocation(item) for item in analysis.generate_invocations]
        if len(invocations) != 1:
            release_error = await self._release(image_reservation, None)
            error = "Error: 视频积分计费要求每次前台 bash 仅生成一个视频，请拆分命令"
            return None, None, f"{error}\n{release_error}" if release_error else error
        invocation = invocations[0]
        missing: list[str] = []
        if not _static_video_value(invocation.model):
            missing.append("model")
        if not _static_video_value(invocation.resolution):
            missing.append("resolution")
        if invocation.duration_seconds is None or invocation.duration_seconds < 4:
            missing.append("duration(>=4)")
        if not _static_video_value(invocation.output_file):
            missing.append("output-file")
        if missing:
            release_error = await self._release(image_reservation, None)
            error = f"Error: 视频积分计费要求明确的 {', '.join(missing)} 参数"
            if all(flag in analysis.command for flag in _BILLING_REQUIRED_FLAGS):
                error += "；命令中已检测到这些参数，可能是反斜杠续行/换行符破坏了解析，请调整命令格式后重试"
            return None, None, f"{error}\n{release_error}" if release_error else error
        try:
            result = await quota_bridge.reserve_video_generation(
                model=invocation.model if _static_video_value(invocation.model) else None,
                resolution=invocation.resolution if _static_video_value(invocation.resolution) else None,
                duration_seconds=invocation.duration_seconds,
                idempotency_key=self._video_idempotency_key(invocation, analysis.command),
                provider=invocation.provider if _static_video_value(invocation.provider) else None,
                output_file=invocation.output_file,
                # 升格（--upscale-video）走 regeneration 端点，按重生成费率计价
                operation="regeneration" if invocation.upscale_video else "generation",
            )
        except Exception as exc:  # noqa: BLE001 - tool boundary converts failures to text
            release_error = await self._release(image_reservation, None)
            suffix = f"\n{release_error}" if release_error else ""
            return None, None, f"Error: Failed to check video generation quota: {exc}{suffix}"
        if not isinstance(result, dict) or not result.get("allowed", False):
            message = result.get("message") if isinstance(result, dict) else None
            release_error = await self._release(image_reservation, None)
            error = f"Error: {message or '视频额度检查返回无效结果'}"
            return None, None, f"{error}\n{release_error}" if release_error else error
        return image_reservation, result.get("reservation"), None

    async def _release(self, image_reservation: object | None, video_reservation: object | None) -> str | None:
        quota_bridge = self._bridge()
        if quota_bridge is None:
            return "Error: Generation quota service is unavailable."
        errors: list[str] = []
        if video_reservation is not None:
            try:
                await quota_bridge.release_video_points(video_reservation)
            except Exception as exc:  # noqa: BLE001 - tool boundary converts failures to text
                errors.append(f"Error: Failed to release unused video generation quota: {exc}")
        if image_reservation is not None:
            try:
                await quota_bridge.release_image_generations(image_reservation)
            except Exception as exc:  # noqa: BLE001 - tool boundary converts failures to text
                errors.append(f"Error: Failed to release unused image generation quota: {exc}")
        return "\n".join(errors) if errors else None

    def _video_idempotency_key(self, invocation: VideoGenerationInvocation, command: str) -> str:
        context = self._runtime.context or {}
        run_id = str(context.get("run_id") or "")
        tool_call_id = str(getattr(self._runtime, "tool_call_id", None) or context.get("tool_call_id") or "")
        seed = f"{run_id}:{tool_call_id}:video-generation" if run_id or tool_call_id else f"{command}:{invocation.output_file or ''}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _resolve_video_invocation(self, invocation: VideoGenerationInvocation) -> VideoGenerationInvocation:
        """Fill fixed 2K/upscale billing fields from the source sidecar."""
        if not invocation.upscale_video:
            return invocation
        source = invocation.upscale_video
        if not _static_video_value(source) or source.startswith(("http://", "https://")):
            return replace(invocation, resolution=invocation.resolution or "2K")
        source_record = self._sidecar_loader(source)
        if not isinstance(source_record, dict):
            return replace(invocation, resolution=invocation.resolution or "2K")
        params = source_record.get("params") if isinstance(source_record.get("params"), dict) else {}
        billing = source_record.get("billing") if isinstance(source_record.get("billing"), dict) else {}
        raw_duration = params.get("duration") or billing.get("billable_duration_seconds") or billing.get("requested_duration_seconds")
        try:
            duration = int(raw_duration) if raw_duration is not None else None
        except (TypeError, ValueError):
            duration = None
        return replace(
            invocation,
            model=invocation.model or source_record.get("model"),
            resolution=invocation.resolution or "2K",
            duration_seconds=invocation.duration_seconds or duration,
        )

    def _with_video_billing_env(self, reservation: object, command: str) -> str:
        values = {
            "DEERFLOW_VIDEO_QUOTA_RESERVATION_ID": getattr(reservation, "reservation_id", None),
            "DEERFLOW_VIDEO_QUOTA_RECORD_ID": getattr(reservation, "record_id", None),
            "DEERFLOW_VIDEO_QUOTA_USAGE_PERIOD_ID": getattr(reservation, "usage_period_id", None),
            "DEERFLOW_VIDEO_QUOTA_IDEMPOTENCY_KEY": getattr(reservation, "idempotency_key", None),
            "DEERFLOW_VIDEO_QUOTA_RUN_ID": (self._runtime.context or {}).get("run_id"),
            "DEERFLOW_VIDEO_QUOTA_THREAD_ID": (self._runtime.context or {}).get("thread_id"),
        }
        assignments = [f"{key}={shlex.quote(str(value))}" for key, value in values.items() if value]
        return f"export {' '.join(assignments)}; {command}" if assignments else command

    async def _finalize_video_points(
        self,
        reservation: object,
        invocation: VideoGenerationInvocation,
        dispatched: bool,
    ) -> str | None:
        quota_bridge = self._bridge()
        if quota_bridge is None or not _is_points_video_reservation(reservation):
            return None
        period_id = getattr(reservation, "usage_period_id", None)
        sidecar = self._sidecar_loader(invocation.output_file)
        if sidecar and (str(sidecar.get("reservation_id") or "") != str(getattr(reservation, "reservation_id", "")) or str(sidecar.get("usage_period_id") or "") != str(period_id or "")):
            sidecar = None
        status = str((sidecar or {}).get("status") or "").lower()
        provider_task_id = (sidecar or {}).get("task_id")
        if not dispatched:
            status = "failed"
        elif status in _VIDEO_SUCCESS_STATUSES:
            try:
                await quota_bridge.settle_video_points(
                    reservation,
                    usage_period_id=period_id,
                    provider_task_id=provider_task_id,
                    billable_duration_seconds=invocation.duration_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - preserve the reservation on accounting failure
                try:
                    await quota_bridge.mark_video_points_pending(
                        reservation,
                        usage_period_id=period_id,
                        provider_task_id=provider_task_id,
                        reason=f"结算失败: {exc}",
                    )
                except Exception:
                    pass
                return f"Error: Video quota settlement is pending: {exc}"
            return None
        if status in _VIDEO_FAILURE_STATUSES:
            try:
                await quota_bridge.release_video_points(
                    reservation,
                    usage_period_id=period_id,
                    reason=status or "provider_failed",
                )
            except Exception as exc:  # noqa: BLE001
                return f"Error: Failed to release video points: {exc}"
            return None
        if sidecar is not None and not provider_task_id:
            # 收据存在但未拿到 provider 任务 ID：provider 调用结果不明确（未提交或响应丢失），
            # 为避免预占永久挂起，直接释放而不是无限等待
            try:
                await quota_bridge.release_video_points(reservation, usage_period_id=period_id, reason="no_provider_task")
            except Exception as exc:  # noqa: BLE001
                return f"Error: Failed to release video points: {exc}"
            return None
        try:
            await quota_bridge.mark_video_points_pending(
                reservation,
                usage_period_id=period_id,
                provider_task_id=provider_task_id,
                reason=status or "status_unknown",
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: Video quota settlement is pending: {exc}"
        return None

    async def _reconcile_video_sidecar(self, invocation: VideoGenerationInvocation) -> str | None:
        if invocation.operation == "generate":
            return None
        quota_bridge = self._bridge()
        if quota_bridge is None:
            return None
        sidecar = self._sidecar_loader(invocation.output_file)
        if not sidecar:
            return None
        reservation_id = sidecar.get("reservation_id")
        period_id = sidecar.get("usage_period_id")
        if not reservation_id or not period_id:
            return None
        status = str(sidecar.get("status") or "").lower()
        try:
            if status in _VIDEO_SUCCESS_STATUSES:
                await quota_bridge.settle_video_points(
                    str(reservation_id),
                    usage_period_id=str(period_id),
                    provider_task_id=sidecar.get("task_id"),
                    billable_duration_seconds=(sidecar.get("params") or {}).get("duration"),
                )
            elif status in _VIDEO_FAILURE_STATUSES:
                await quota_bridge.release_video_points(str(reservation_id), usage_period_id=str(period_id), reason=status)
        except Exception as exc:  # noqa: BLE001 - query output remains useful
            return f"Error: Failed to reconcile video quota: {exc}"
        return None
