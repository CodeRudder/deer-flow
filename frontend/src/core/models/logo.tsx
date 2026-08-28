import { SparklesIcon } from "lucide-react";
import { type CSSProperties, type ComponentProps, useState } from "react";

import { cn } from "@/lib/utils";

const MODEL_LOGO_PATHS = {
  "alibaba-cn": "/logos/alibaba-cn.svg",
  anthropic: "/logos/anthropic.svg",
  deepseek: "/logos/deepseek.svg",
  doubao: "/logos/doubao.svg",
  google: "/logos/google.svg",
  llama: "/logos/llama.svg",
  minimax: "/logos/minimax.svg",
  mistral: "/logos/mistral.svg",
  "moonshotai-cn": "/logos/moonshotai-cn.svg",
  openai: "/logos/openai.svg",
  zhipuai: "/logos/zhipuai.svg",
} as const;

export type ModelLogoProvider = keyof typeof MODEL_LOGO_PATHS;

const MODEL_LOGO_PROVIDER_ALIASES: Record<string, ModelLogoProvider> = {
  alibaba: "alibaba-cn",
  "google-vertex": "google",
  "google-vertex-anthropic": "anthropic",
  moonshotai: "moonshotai-cn",
  zai: "zhipuai",
  "zai-coding-plan": "zhipuai",
  "zhipuai-coding-plan": "zhipuai",
};

export function getModelLogoPath(provider: string): string | undefined {
  const normalizedProvider = provider.trim().toLowerCase();
  const resolvedProvider =
    MODEL_LOGO_PROVIDER_ALIASES[normalizedProvider] ?? normalizedProvider;

  return MODEL_LOGO_PATHS[resolvedProvider as ModelLogoProvider];
}

const PROVIDER_KEYWORD_RULES: Array<[keywords: string[], provider: ModelLogoProvider]> = [
  [["kimi", "moonshot"], "moonshotai-cn"],
  [["doubao", "volcengine", "volcano", "seed"], "doubao"],
  [["gpt", "openai"], "openai"],
  [["claude", "anthropic"], "anthropic"],
  [["gemini", "google"], "google"],
  [["deepseek"], "deepseek"],
  [["qwen", "alibaba"], "alibaba-cn"],
  [["zhipu", "glm"], "zhipuai"],
  [["minimax"], "minimax"],
  [["mistral"], "mistral"],
  [["llama", "meta"], "llama"],
];

export const PROVIDER_LOGO_COLORS: Record<ModelLogoProvider, string> = {
  "moonshotai-cn": "#111827",
  doubao: "#2563eb",
  openai: "#10a37f",
  anthropic: "#d97757",
  google: "#4285f4",
  deepseek: "#4d6bfe",
  "alibaba-cn": "#ff6a00",
  zhipuai: "#315cec",
  minimax: "#7c3aed",
  mistral: "#ff7000",
  llama: "#0467df",
};

export function getModelLogoColor(provider: ModelLogoProvider): string {
  return PROVIDER_LOGO_COLORS[provider];
}

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
} & Omit<ComponentProps<"img">, "src" | "alt">) {
  const provider = resolveProviderFromModelName(name, displayName);
  const logoPath = provider ? getModelLogoPath(provider) : undefined;
  const [failed, setFailed] = useState(false);

  if (!logoPath || failed) {
    return (
      <span
        aria-label="model logo"
        className={cn(
          "inline-flex size-6 shrink-0 items-center justify-center rounded-md border bg-background",
          className,
        )}
        role="img"
      >
        <SparklesIcon className="size-3.5 text-[#f59e0b]" />
      </span>
    );
  }

  const logoStyle = {
    "--model-logo-color": PROVIDER_LOGO_COLORS[provider!],
    "--model-logo-url": `url("${logoPath}")`,
  } as CSSProperties;

  return (
    <span
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-md border bg-background",
        className,
      )}
    >
      <img
        {...props}
        alt={`${name} logo`}
        className="size-4 dark:invert"
        height={16}
        onError={() => setFailed(true)}
        src={logoPath}
        style={logoStyle}
        width={16}
      />
    </span>
  );
}
