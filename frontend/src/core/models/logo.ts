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

type ModelLogoProvider = keyof typeof MODEL_LOGO_PATHS;

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
