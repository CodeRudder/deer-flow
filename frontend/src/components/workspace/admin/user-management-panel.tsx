"use client";

import {
  Clock3,
  MailWarning,
  Pencil,
  Search,
  UserCheck,
  Users,
  UserX,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAdminUserManagement,
  useAdminUserStatusAction,
  useRetryAdminUserApprovalEmail,
  useUpdateAdminUserEmail,
} from "@/core/admin/hooks";
import {
  ACCOUNT_STATUS,
  APPROVAL_EMAIL_STATUS,
  USER_STATUS_FILTER,
  type AccountStatus,
  type AdminUser,
  type AdminUserSummary,
  type UserStatusFilter,
} from "@/core/admin/types";
import { cn } from "@/lib/utils";

const filters: Array<[UserStatusFilter, string]> = [
  [ACCOUNT_STATUS.ACTIVE, "可用"],
  [ACCOUNT_STATUS.PENDING, "待审批"],
  [ACCOUNT_STATUS.DISABLED, "已禁用"],
  [USER_STATUS_FILTER.ALL, "全部"],
];

const statusCopy = {
  [ACCOUNT_STATUS.ACTIVE]: {
    label: "可用",
    className:
      "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  },
  [ACCOUNT_STATUS.PENDING]: {
    label: "待审批",
    className:
      "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  [ACCOUNT_STATUS.DISABLED]: {
    label: "已禁用",
    className:
      "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
  },
} as const satisfies Record<
  AccountStatus,
  { label: string; className: string }
>;

const actionCopy = {
  approve: {
    title: "通过注册申请",
    description: "通过后账号立即可以登录，系统随后尝试向注册邮箱发送通知。",
    button: "确认通过",
  },
  disable: {
    title: "禁用用户",
    description: "禁用后用户将无法登录或发起新请求，但不会终止已经运行的任务。",
    button: "确认禁用",
  },
  enable: {
    title: "恢复用户",
    description: "恢复后用户可以重新登录，禁用前签发的旧会话仍然无效。",
    button: "确认恢复",
  },
} as const;

type StatusAction = keyof typeof actionCopy;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatCreatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "-";

  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function SummaryCards({ summary }: { summary?: AdminUserSummary }) {
  const cards = [
    {
      label: "用户总数",
      note: "全部角色与注册来源",
      value: summary?.total ?? 0,
      icon: Users,
      tone: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
    },
    {
      label: "可用",
      note: "可正常访问平台",
      value: summary?.active ?? 0,
      icon: UserCheck,
      tone: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    },
    {
      label: "待审批",
      note: "等待管理员通过",
      value: summary?.pending ?? 0,
      icon: Clock3,
      tone: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
    },
    {
      label: "已禁用",
      note: "当前无法访问平台",
      value: summary?.disabled ?? 0,
      icon: UserX,
      tone: "bg-slate-100 text-slate-700 dark:bg-slate-900 dark:text-slate-300",
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map(({ label, note, value, icon: Icon, tone }) => (
        <article
          key={label}
          className="bg-background rounded-lg border p-4 shadow-xs"
        >
          <div className="text-muted-foreground flex items-center justify-between text-xs">
            <span>{label}</span>
            <span
              className={cn("grid size-8 place-items-center rounded-md", tone)}
            >
              <Icon className="size-4" aria-hidden="true" />
            </span>
          </div>
          <div className="mt-3 text-2xl font-semibold tabular-nums">
            {value}
          </div>
          <div className="text-muted-foreground mt-1 text-xs">{note}</div>
        </article>
      ))}
    </div>
  );
}

function StatusBadge({ user }: { user: AdminUser }) {
  const copy = statusCopy[user.account_status];
  return (
    <Badge variant="outline" className={copy.className}>
      {copy.label}
    </Badge>
  );
}

function RoleBadge({ user }: { user: AdminUser }) {
  return (
    <Badge
      variant="outline"
      className={
        user.role === "admin"
          ? "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
          : undefined
      }
    >
      {user.role === "admin" ? "管理员" : "普通用户"}
    </Badge>
  );
}

function SourceBadge({ user }: { user: AdminUser }) {
  const labels = { local: "本地", oidc: "OIDC", platform: "平台" } as const;
  return (
    <Badge
      variant="secondary"
      title={user.source_provider ?? undefined}
      className="font-normal"
    >
      {labels[user.source]}
    </Badge>
  );
}

function UserTableSkeleton() {
  return (
    <div className="space-y-2 py-3">
      {Array.from({ length: 5 }, (_, index) => (
        <Skeleton key={index} className="h-14 w-full" />
      ))}
    </div>
  );
}

export function UserManagementPanel() {
  const [status, setStatus] = useState<UserStatusFilter>(ACCOUNT_STATUS.ACTIVE);
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const [candidate, setCandidate] = useState<{
    user: AdminUser;
    action: StatusAction;
  } | null>(null);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [emailConfirmation, setEmailConfirmation] = useState<{
    userId: string;
    previousEmail: string;
    nextEmail: string;
  } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedKeyword(keyword.trim()),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    setPage(1);
  }, [debouncedKeyword, status]);

  const management = useAdminUserManagement({
    status,
    keyword: debouncedKeyword || undefined,
    page,
    page_size: pageSize,
  });
  const statusMutation = useAdminUserStatusAction();
  const retryMutation = useRetryAdminUserApprovalEmail();
  const emailMutation = useUpdateAdminUserEmail();
  const totalPages = Math.max(
    1,
    Math.ceil((management.users.data?.total ?? 0) / pageSize),
  );
  const activeRowId =
    statusMutation.isPending && statusMutation.variables
      ? statusMutation.variables.userId
      : retryMutation.isPending
        ? retryMutation.variables
        : emailMutation.isPending && emailMutation.variables
          ? emailMutation.variables.userId
          : null;
  const heading = useMemo(
    () =>
      ({
        [ACCOUNT_STATUS.ACTIVE]: ["可用用户", "按创建时间倒序"],
        [ACCOUNT_STATUS.PENDING]: ["待审批用户", "最早申请优先"],
        [ACCOUNT_STATUS.DISABLED]: ["已禁用用户", "按创建时间倒序"],
        [USER_STATUS_FILTER.ALL]: ["全部用户", "按创建时间倒序"],
      })[status],
    [status],
  );

  const selectStatus = (nextStatus: UserStatusFilter) => {
    setStatus(nextStatus);
    setPage(1);
  };

  const runStatusAction = async () => {
    if (!candidate) return;
    const { user, action } = candidate;
    try {
      const updated = await statusMutation.mutateAsync({
        userId: user.id,
        action,
      });
      setCandidate(null);
      if (action === "approve") {
        if (updated.approval_email_status === APPROVAL_EMAIL_STATUS.FAILED) {
          toast.warning("账号已通过，通知邮件发送失败，可在操作区手动重发");
        } else {
          toast.success("审批已通过，用户现在可以登录");
        }
      } else if (action === "disable") {
        toast.success("用户已禁用");
      } else {
        toast.success("用户已恢复为可用状态");
      }
    } catch (error) {
      toast.error(errorMessage(error, "用户状态更新失败"));
    }
  };

  const retryApprovalEmail = async (user: AdminUser) => {
    try {
      await retryMutation.mutateAsync(user.id);
      toast.success(`通知邮件已重新发送至 ${user.email}`);
    } catch (error) {
      toast.error(errorMessage(error, "通知邮件重发失败"));
    }
  };

  const openEditor = (user: AdminUser) => {
    setEditingUser(user);
    setEmail(user.email);
    setEmailError("");
  };

  const saveEmail = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingUser) return;

    const form = event.currentTarget;
    const normalizedEmail = email.trim();
    if (!form.checkValidity()) {
      setEmailError("请输入完整、有效的邮箱地址");
      return;
    }
    if (normalizedEmail.toLowerCase() === editingUser.email.toLowerCase()) {
      setEditingUser(null);
      setEmailError("");
      toast.success("用户信息未变更，新邮箱与当前邮箱一致");
      return;
    }

    setEmailConfirmation({
      userId: editingUser.id,
      previousEmail: editingUser.email,
      nextEmail: normalizedEmail,
    });
  };

  const confirmEmailUpdate = async () => {
    if (!emailConfirmation) return;

    try {
      const updated = await emailMutation.mutateAsync({
        userId: emailConfirmation.userId,
        email: emailConfirmation.nextEmail,
      });
      setEmailConfirmation(null);
      setEditingUser(null);
      setEmailError("");
      toast.success(
        `${emailConfirmation.previousEmail} 已改为 ${updated.email}，用户需使用新邮箱重新登录`,
      );
    } catch (error) {
      setEmailConfirmation(null);
      setEmailError(errorMessage(error, "用户邮箱更新失败"));
    }
  };

  return (
    <div className="space-y-5">
      <SummaryCards summary={management.summary.data} />

      {!management.summary.data?.registration_approval_enabled &&
      (management.summary.data?.pending ?? 0) > 0 ? (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          新注册当前会自动通过；列表中仍有历史待审批用户，可以继续处理。
        </div>
      ) : null}

      <section className="bg-background rounded-lg border p-4 shadow-xs">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 className="font-medium">{heading[0]}</h2>
            <p className="text-muted-foreground text-sm">{heading[1]}</p>
          </div>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:flex-wrap">
            <div className="flex overflow-x-auto rounded-md border p-1">
              {filters.map(([value, label]) => (
                <Button
                  key={value}
                  type="button"
                  size="sm"
                  variant={status === value ? "secondary" : "ghost"}
                  aria-pressed={status === value}
                  onClick={() => selectStatus(value)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <div className="relative">
              <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
              <Input
                type="search"
                className="w-full pl-8 sm:w-64"
                placeholder="搜索用户 ID 或邮箱"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </div>
          </div>
        </div>

        {(management.summary.isError || management.users.isError) && (
          <div className="border-destructive/40 bg-destructive/5 text-destructive mt-4 flex items-center justify-between gap-3 rounded-md border px-4 py-3 text-sm">
            <span>用户数据加载失败，请稍后重试</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                void management.summary.refetch();
                void management.users.refetch();
              }}
            >
              重试
            </Button>
          </div>
        )}

        <div className="mt-4 overflow-x-auto">
          {management.users.isLoading ? (
            <UserTableSkeleton />
          ) : (
            <table className="w-full min-w-[960px] table-fixed text-sm">
              <colgroup>
                <col className="w-[20%]" />
                <col className="w-[22%]" />
                <col className="w-[10%]" />
                <col className="w-[9%]" />
                <col className="w-[15%]" />
                <col className="w-[9%]" />
                <col className="w-[15%]" />
              </colgroup>
              <thead className="text-muted-foreground border-b text-left">
                <tr>
                  <th className="py-2 pr-2 font-medium">用户 ID</th>
                  <th className="px-2 font-medium">邮箱</th>
                  <th className="px-2 font-medium">角色</th>
                  <th className="px-2 font-medium">来源</th>
                  <th className="px-2 font-medium">创建日期</th>
                  <th className="px-2 font-medium">状态</th>
                  <th className="py-2 pl-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {(management.users.data?.items ?? []).map((user) => {
                  const createdAt = formatCreatedAt(user.created_at);
                  const isProcessing = activeRowId === user.id;
                  const canEdit =
                    user.role !== "admin" &&
                    user.source === "local" &&
                    user.allowed_actions.includes("edit");
                  const canRetry =
                    user.role === "user" &&
                    user.allowed_actions.includes("retry_approval_email");
                  const statusAction =
                    user.role === "user"
                      ? (["approve", "disable", "enable"] as const).find(
                          (action) => user.allowed_actions.includes(action),
                        )
                      : undefined;
                  return (
                    <tr key={user.id} className="h-14 border-b last:border-0">
                      <td className="py-3.5 pr-2">
                        <span
                          className="block truncate font-mono text-xs"
                          title={user.id}
                        >
                          {user.id}
                        </span>
                      </td>
                      <td className="px-2 font-medium">
                        <span className="block truncate" title={user.email}>
                          {user.email}
                        </span>
                      </td>
                      <td className="px-2">
                        <RoleBadge user={user} />
                      </td>
                      <td className="px-2">
                        <SourceBadge user={user} />
                      </td>
                      <td className="text-muted-foreground px-2 whitespace-nowrap">
                        {createdAt}
                      </td>
                      <td className="px-2">
                        <StatusBadge user={user} />
                      </td>
                      <td className="py-2.5 pl-2">
                        <div className="flex flex-wrap items-center gap-1">
                          {canEdit ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className="h-7 border-slate-700 bg-slate-700 px-2.5 text-xs text-white shadow-none hover:border-slate-800 hover:bg-slate-800 hover:text-white dark:border-slate-500 dark:bg-slate-500 dark:hover:border-slate-400 dark:hover:bg-slate-400"
                              disabled={isProcessing}
                              onClick={() => openEditor(user)}
                            >
                              <Pencil className="size-3" />
                              编辑
                            </Button>
                          ) : null}
                          {canRetry ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className="h-7 border-amber-700 bg-amber-700 px-2.5 text-xs text-white shadow-none hover:border-amber-800 hover:bg-amber-800 hover:text-white dark:border-amber-600 dark:bg-amber-600 dark:hover:border-amber-500 dark:hover:bg-amber-500"
                              disabled={isProcessing}
                              onClick={() => void retryApprovalEmail(user)}
                            >
                              <MailWarning className="size-3" />
                              重发通知
                            </Button>
                          ) : null}
                          {statusAction ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className={cn(
                                "h-7 px-2.5 text-xs shadow-none",
                                statusAction === "disable"
                                  ? "border-rose-700 bg-rose-700 text-white hover:border-rose-800 hover:bg-rose-800 hover:text-white dark:border-rose-600 dark:bg-rose-600 dark:hover:border-rose-500 dark:hover:bg-rose-500"
                                  : statusAction === "approve"
                                    ? "border-emerald-700 bg-emerald-700 text-white hover:border-emerald-800 hover:bg-emerald-800 hover:text-white dark:border-emerald-600 dark:bg-emerald-600 dark:hover:border-emerald-500 dark:hover:bg-emerald-500"
                                    : "border-slate-700 bg-slate-700 text-white hover:border-slate-800 hover:bg-slate-800 hover:text-white dark:border-slate-500 dark:bg-slate-500 dark:hover:border-slate-400 dark:hover:bg-slate-400",
                              )}
                              disabled={isProcessing}
                              onClick={() =>
                                setCandidate({ user, action: statusAction })
                              }
                            >
                              {statusAction === "approve"
                                ? "通过"
                                : statusAction === "disable"
                                  ? "禁用"
                                  : "恢复"}
                            </Button>
                          ) : user.role !== "admin" && !canEdit && !canRetry ? (
                            <span className="text-muted-foreground text-xs">
                              -
                            </span>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {!management.users.isLoading &&
          !management.users.isError &&
          !(management.users.data?.items.length ?? 0) ? (
            <div className="text-muted-foreground py-12 text-center text-sm">
              <UserCheck className="mx-auto mb-3 size-8 opacity-40" />
              <p className="text-foreground font-medium">
                {debouncedKeyword ? "没有匹配的用户" : "当前状态下暂无用户"}
              </p>
              <p className="mt-1">
                {debouncedKeyword
                  ? "请调整用户 ID 或邮箱关键词"
                  : "可切换其他状态查看"}
              </p>
            </div>
          ) : null}
        </div>

        <div className="text-muted-foreground mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4 text-sm">
          <span>共 {management.users.data?.total ?? 0} 位用户</span>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="bg-background h-8 rounded-md border px-2"
              aria-label="每页用户数量"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
            >
              {[15, 50, 100].map((size) => (
                <option key={size} value={size}>
                  每页 {size} 条
                </option>
              ))}
            </select>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={page <= 1 || management.users.isFetching}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              上一页
            </Button>
            <span>
              第 {page} / {totalPages} 页
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={page >= totalPages || management.users.isFetching}
              onClick={() => setPage((current) => current + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </section>

      <Dialog
        open={candidate !== null}
        onOpenChange={(open) => {
          if (!open && !statusMutation.isPending) setCandidate(null);
        }}
      >
        <DialogContent showCloseButton={!statusMutation.isPending}>
          <DialogHeader>
            <DialogTitle>
              {candidate ? actionCopy[candidate.action].title : "变更用户状态"}
            </DialogTitle>
            <DialogDescription>
              {candidate ? actionCopy[candidate.action].description : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="bg-muted/50 rounded-lg border px-4 py-3">
            <div className="font-medium">{candidate?.user.email}</div>
            <div className="text-muted-foreground mt-1 truncate font-mono text-xs">
              {candidate?.user.id}
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={statusMutation.isPending}
              onClick={() => setCandidate(null)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant={
                candidate?.action === "disable" ? "destructive" : "default"
              }
              disabled={!candidate || statusMutation.isPending}
              onClick={() => void runStatusAction()}
            >
              {statusMutation.isPending
                ? "处理中..."
                : candidate
                  ? actionCopy[candidate.action].button
                  : "确认"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={emailConfirmation !== null}
        onOpenChange={(open) => {
          if (!open && !emailMutation.isPending) setEmailConfirmation(null);
        }}
      >
        <DialogContent showCloseButton={!emailMutation.isPending}>
          <DialogHeader className="gap-1">
            <DialogTitle>确认修改邮箱？</DialogTitle>
            <DialogDescription>
              保存后旧会话将失效，用户需要使用新邮箱和原密码重新登录。
            </DialogDescription>
          </DialogHeader>
          <div className="bg-muted/50 space-y-2 rounded-lg border px-4 py-3 text-sm">
            <div className="text-muted-foreground truncate">
              当前邮箱：
              <span className="text-foreground ml-1 font-medium">
                {emailConfirmation?.previousEmail}
              </span>
            </div>
            <div className="text-muted-foreground truncate">
              新邮箱：
              <span className="text-foreground ml-1 font-medium">
                {emailConfirmation?.nextEmail}
              </span>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={emailMutation.isPending}
              onClick={() => setEmailConfirmation(null)}
            >
              返回修改
            </Button>
            <Button
              type="button"
              disabled={!emailConfirmation || emailMutation.isPending}
              onClick={() => void confirmEmailUpdate()}
            >
              {emailMutation.isPending ? "提交中..." : "确认提交"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={editingUser !== null}
        onOpenChange={(open) => {
          if (!open && !emailMutation.isPending) {
            setEditingUser(null);
            setEmailError("");
          }
        }}
      >
        <DialogContent showCloseButton={!emailMutation.isPending}>
          <form
            onSubmit={(event) => void saveEmail(event)}
            className="contents"
          >
            <DialogHeader className="gap-1">
              <DialogTitle>编辑用户信息</DialogTitle>
              <DialogDescription>当前仅支持修改邮箱。</DialogDescription>
            </DialogHeader>
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">新邮箱</span>
              <Input
                type="email"
                required
                autoComplete="off"
                value={email}
                aria-invalid={Boolean(emailError)}
                aria-describedby="admin-user-email-help admin-user-email-error"
                disabled={emailMutation.isPending}
                onChange={(event) => {
                  setEmail(event.target.value);
                  setEmailError("");
                }}
              />
            </label>
            <p
              id="admin-user-email-help"
              className="text-muted-foreground text-xs leading-5"
            >
              仅支持允许的企业邮箱；保存后旧会话失效，用户需用新邮箱和原密码重新登录。
            </p>
            {emailError ? (
              <div
                id="admin-user-email-error"
                className="text-destructive text-sm"
                role="alert"
              >
                {emailError}
              </div>
            ) : null}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={emailMutation.isPending}
                onClick={() => {
                  setEditingUser(null);
                  setEmailError("");
                }}
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={emailMutation.isPending || !email.trim()}
              >
                {emailMutation.isPending ? "保存中..." : "保存修改"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
