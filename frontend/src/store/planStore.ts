import { create } from 'zustand';

interface PlanStore {
  latestPlan: any | null;
  latestScan: any | null;
  setLatestPlan: (plan: any | null) => void;
  setLatestScan: (scan: any | null) => void;
}

/** Holds coach plans in memory so we don't stuff huge JSON into URL params (crashes navigation). */
export const usePlanStore = create<PlanStore>((set) => ({
  latestPlan: null,
  latestScan: null,
  setLatestPlan: (plan) => set({ latestPlan: plan }),
  setLatestScan: (scan) => set({ latestScan: scan }),
}));
