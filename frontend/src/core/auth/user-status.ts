export const ACCOUNT_STATUS = {
  ACTIVE: "active",
  PENDING: "pending",
  DISABLED: "disabled",
} as const;

export const ACCOUNT_STATUS_VALUES = [
  ACCOUNT_STATUS.ACTIVE,
  ACCOUNT_STATUS.PENDING,
  ACCOUNT_STATUS.DISABLED,
] as const;

export type AccountStatus =
  (typeof ACCOUNT_STATUS)[keyof typeof ACCOUNT_STATUS];

export const APPROVAL_EMAIL_STATUS = {
  PENDING: "pending",
  SENDING: "sending",
  SENT: "sent",
  FAILED: "failed",
} as const;

export const APPROVAL_EMAIL_STATUS_VALUES = [
  APPROVAL_EMAIL_STATUS.PENDING,
  APPROVAL_EMAIL_STATUS.SENDING,
  APPROVAL_EMAIL_STATUS.SENT,
  APPROVAL_EMAIL_STATUS.FAILED,
] as const;

export type ApprovalEmailStatus =
  (typeof APPROVAL_EMAIL_STATUS)[keyof typeof APPROVAL_EMAIL_STATUS];

export const USER_STATUS_FILTER = {
  ...ACCOUNT_STATUS,
  ALL: "all",
} as const;

export type UserStatusFilter =
  (typeof USER_STATUS_FILTER)[keyof typeof USER_STATUS_FILTER];
