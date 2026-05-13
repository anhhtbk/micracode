"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import {
  getModelCatalog,
  type ModelCatalog,
} from "@/lib/api/models";

/**
 * Persisted chat model selection.
 *
 * The store owns:
 *   - the user's chosen `{provider, model}` (persisted to localStorage),
 *   - the currently-loaded server catalog (volatile),
 *   - a `loadCatalog` bootstrap that reconciles persisted selection
 *     against what the server now offers.
 *
 * The picker component reads + writes `provider`/`model`; the chat
 * panel reads them at request time via `useModelStore.getState()` so
 * swapping models mid-session does not recreate the `useChat` transport.
 */

interface PersistedSlice {
  provider: string | null;
  model: string | null;
}

interface ModelStoreState extends PersistedSlice {
  catalog: ModelCatalog | null;
  isLoading: boolean;
  error: string | null;
  setSelection: (provider: string, model: string) => void;
  loadCatalog: () => Promise<void>;
}

function isPairInCatalog(
  catalog: ModelCatalog,
  provider: string | null,
  model: string | null,
): boolean {
  if (!provider || !model) return false;
  const p = catalog.providers.find((pp) => pp.id === provider);
  if (!p) return false;
  return p.models.some((m) => m.id === model);
}

const PERSIST_KEY = "oe:selected-model";

export const useModelStore = create<ModelStoreState>()(
  persist(
    (set, get) => ({
      provider: null,
      model: null,
      catalog: null,
      isLoading: false,
      error: null,

      setSelection: (provider, model) => set({ provider, model }),

      loadCatalog: async () => {
        if (get().isLoading) return;
        set({ isLoading: true, error: null });
        try {
          const catalog = await getModelCatalog();
          const { provider, model } = get();
          const keep = !catalog.locked && isPairInCatalog(catalog, provider, model);
          set({
            catalog,
            provider: keep ? provider : catalog.default.provider,
            model: keep ? model : catalog.default.model,
            isLoading: false,
          });
        } catch (err) {
          set({
            isLoading: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      },
    }),
    {
      name: PERSIST_KEY,
      storage: createJSONStorage(() => localStorage),
      partialize: (state): PersistedSlice => ({
        provider: state.provider,
        model: state.model,
      }),
    },
  ),
);
