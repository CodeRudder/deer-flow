const numberFormat = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 2,
});

/**
 * 积分/次数展示格式：最多 2 位小数、不用缩写。
 * 积分是钱等价物且后端拦截文案按小数输出，四舍五入或 K/M 缩写
 * 会造成展示数字与拦截数字不一致。
 */
export function formatPoints(value: number): string {
  return numberFormat.format(value);
}
