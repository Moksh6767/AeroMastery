export type Assignment = {
  task_id: string;
  aircraft_id: string;
  crew_id: string;
  start: number;
  end: number;
  base: string;
  reason_codes: string[];
  explanation: string;
};
export type Metrics = {
  coverage: number;
  weighted_coverage: number;
  risk: number;
  planning_seconds: number;
  assets_utilized: number;
  crew_duty_violations: number;
  changes_per_event: number;
  [key: string]: number;
};
export type Plan = {
  method: string;
  status: string;
  assignments: Assignment[];
  unscheduled: {
    task_id: string;
    reason_codes: string[];
    explanation: string;
  }[];
  metrics: Metrics;
  baseline?: { metrics: Metrics };
  previous_metrics?: Metrics;
};
export type PlanVersion = {
  id: string;
  number: number;
  approved: boolean;
  plan: Plan;
};
export type World = {
  label: string;
  bases: {
    id: string;
    longitude: number;
    latitude: number;
    closed_until: number;
  }[];
  aircraft: {
    id: string;
    base: string;
    kind: string;
    ready_at: number;
    field_provenance?: Record<string, unknown>;
  }[];
  crews: { id: string; ready_at: number; duty_limit: number }[];
  tasks: {
    id: string;
    kind: string;
    priority: number;
    base: string;
    destination: [number, number];
    earliest: number;
    deadline: number;
  }[];
  zones: { id: string; until: number; polygon: [number, number][] }[];
};
export type Proposal = {
  id: string;
  status: string;
  revision: number;
  event: { type: string; target: string; minutes: number; priority: number };
  options: {
    strategy: string;
    tradeoff: string;
    plan: Plan;
    diff: {
      added: Assignment[];
      removed: Assignment[];
      changed: { before: Assignment; after: Assignment }[];
    };
  }[];
};
export type State = {
  scenario: {
    id: string;
    preset: string;
    seed: number;
    revision: number;
    world: World;
    fusion: {
      timestamp: string;
      sources: Record<
        string,
        { age_minutes: number; freshness: number; stale: boolean }
      >;
      conflicts: {
        record_id: string;
        field: string;
        resolution: string;
        chosen_source: string | null;
      }[];
    };
  };
  plan: PlanVersion | null;
  proposals: Proposal[];
  predictions: {
    aircraft: {
      aircraft_id: string;
      unserviceability_probability: number;
      model: string;
    }[];
    weather: { base_id: string; risk: number }[];
  };
};
