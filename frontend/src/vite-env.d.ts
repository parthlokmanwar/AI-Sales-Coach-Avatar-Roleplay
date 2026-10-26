/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEEPGRAM_API_KEY: string
  readonly VITE_SARVAM_API_KEY: string
  readonly VITE_USE_SARVAM?: string   // "true" | "false" — defaults to "true"
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
