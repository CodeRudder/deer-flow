// Deep component imports: the package barrel (and even the per-icon index)
// drags in @lobehub/ui via the Avatar/Combine features, whose emoji-data JSON
// import Node ESM cannot load when the test runner externalizes it. The bare
// Mono/Color component files only depend on style helpers and react.
import AlibabaColor from "@lobehub/icons/es/alibaba/components/Color";
import AlibabaMono from "@lobehub/icons/es/alibaba/components/Mono";
import ClaudeColor from "@lobehub/icons/es/claude/components/Color";
import ClaudeMono from "@lobehub/icons/es/claude/components/Mono";
import DeepSeekColor from "@lobehub/icons/es/deepseek/components/Color";
import DeepSeekMono from "@lobehub/icons/es/deepseek/components/Mono";
import DoubaoColor from "@lobehub/icons/es/doubao/components/Color";
import DoubaoMono from "@lobehub/icons/es/doubao/components/Mono";
import GoogleColor from "@lobehub/icons/es/google/components/Color";
import GoogleMono from "@lobehub/icons/es/google/components/Mono";
import MetaColor from "@lobehub/icons/es/meta/components/Color";
import MetaMono from "@lobehub/icons/es/meta/components/Mono";
import MinimaxColor from "@lobehub/icons/es/minimax/components/Color";
import MinimaxMono from "@lobehub/icons/es/minimax/components/Mono";
import MistralColor from "@lobehub/icons/es/mistral/components/Color";
import MistralMono from "@lobehub/icons/es/mistral/components/Mono";
import MoonshotMono from "@lobehub/icons/es/moonshot/components/Mono";
import OpenAIMono from "@lobehub/icons/es/openai/components/Mono";
import ZhipuColor from "@lobehub/icons/es/zhipu/components/Color";
import ZhipuMono from "@lobehub/icons/es/zhipu/components/Mono";
import { SparklesIcon } from "lucide-react";
import { type ComponentProps, type ComponentType } from "react";

import { cn } from "@/lib/utils";

export type ModelLogoProvider =
  | "alibaba-cn"
  | "anthropic"
  | "deepseek"
  | "doubao"
  | "google"
  | "llama"
  | "minimax"
  | "mistral"
  | "moonshotai-cn"
  | "openai"
  | "zhipuai";

type LobeIcon = ComponentType<{ size?: string | number }>;

const LOBE_PROVIDER_ICONS: Record<
  ModelLogoProvider,
  { mono: LobeIcon; color?: LobeIcon }
> = {
  "alibaba-cn": { color: AlibabaColor, mono: AlibabaMono },
  // claude/anthropic 模型一律展示 Claude 星芒标，而非 Anthropic 的 A 标
  anthropic: { color: ClaudeColor, mono: ClaudeMono },
  deepseek: { color: DeepSeekColor, mono: DeepSeekMono },
  doubao: { color: DoubaoColor, mono: DoubaoMono },
  google: { color: GoogleColor, mono: GoogleMono },
  llama: { color: MetaColor, mono: MetaMono },
  minimax: { color: MinimaxColor, mono: MinimaxMono },
  mistral: { color: MistralColor, mono: MistralMono },
  "moonshotai-cn": { mono: MoonshotMono },
  openai: { mono: OpenAIMono },
  zhipuai: { color: ZhipuColor, mono: ZhipuMono },
};

const PROVIDER_KEYWORD_RULES: Array<
  [keywords: string[], provider: ModelLogoProvider]
> = [
  [["kimi", "moonshot"], "moonshotai-cn"],
  [["doubao", "volcengine", "volcano", "seed"], "doubao"],
  [["gpt", "openai"], "openai"],
  [["claude", "anthropic"], "anthropic"],
  [["gemini", "google"], "google"],
  [["deepseek"], "deepseek"],
  [["qwen", "wan", "alibaba"], "alibaba-cn"],
  [["zhipu", "glm"], "zhipuai"],
  [["minimax"], "minimax"],
  [["mistral"], "mistral"],
  [["llama", "meta"], "llama"],
];

export function resolveProviderFromModelName(
  name: string,
  displayName?: string | null,
): ModelLogoProvider | null {
  const value = `${name} ${displayName ?? ""}`.toLowerCase();
  for (const [keywords, provider] of PROVIDER_KEYWORD_RULES) {
    if (keywords.some((kw) => value.includes(kw))) {
      return provider;
    }
  }
  return null;
}

export function ModelProviderLogo({
  name,
  displayName,
  className,
  ...props
}: {
  name: string;
  displayName?: string | null;
} & Omit<ComponentProps<"span">, "children">) {
  const provider = resolveProviderFromModelName(name, displayName);
  const icons = provider ? LOBE_PROVIDER_ICONS[provider] : null;
  const BrandIcon = icons?.color ?? icons?.mono;

  if (!provider || !BrandIcon) {
    return (
      <span
        aria-label="model logo"
        className={cn(
          "inline-flex size-4 shrink-0 items-center justify-center",
          className,
        )}
        role="img"
        {...props}
      >
        <SparklesIcon className="size-3.5 text-[#f59e0b]" />
      </span>
    );
  }

  return (
    <span
      aria-label="model logo"
      data-provider={provider}
      className={cn(
        "inline-flex size-4 shrink-0 items-center justify-center",
        className,
      )}
      role="img"
      {...props}
    >
      <BrandIcon size={16} />
    </span>
  );
}
