"use client";

import { useEffect, useState } from "react";

import type {
  ProposalApplyOutcome,
  ProposalDetail,
  ProposalPreview,
  TripDetail,
} from "../../lib/api";
import { travelApi } from "../../lib/api";
import ProposalPanel from "./proposal-panel";

const trip: TripDetail = {
  id: "proposal-fixture-trip",
  revision: 4,
  title: "Proposal lifecycle browser fixture",
  start_date: "2026-10-04",
  end_date: "2026-10-04",
  timezone: "UTC",
  day_count: 1,
  item_count: 0,
  created_at: "2026-10-04T12:00:00Z",
  updated_at: "2026-10-04T12:00:00Z",
  days: [{
    id: "proposal-fixture-day",
    day_index: 1,
    date: "2026-10-04",
    title: null,
    items: [],
  }],
};

const preview: ProposalPreview = {
  trip_handle: "h_fixture_trip_0001",
  base_trip_revision: 4,
  place_revisions: [],
  operation_count: 1,
  before: [{
    handle: "h_fixture_day_0001",
    day_index: 1,
    date: "2026-10-04",
    title: null,
    items: [],
  }],
  after: [{
    handle: "h_fixture_day_0001",
    day_index: 1,
    date: "2026-10-04",
    title: null,
    items: [{
      handle: "h_fixture_item_0001",
      title: "Preview activity",
      item_type: "activity",
      status: "tentative",
      sort_order: 0,
      start_time: "10:00",
      end_time: "11:00",
      place_handle: "h_fixture_place_0001",
      reservation_handle: null,
    }],
  }],
  diff: [{
    operation_index: 0,
    kind: "add_item",
    before: null,
    after: {
      title: "Preview activity",
      item_type: "activity",
      start_time: "10:00",
      end_time: "11:00",
      day_index: 1,
      date: "2026-10-04",
      sort_order: 0,
    },
  }],
  warnings: [],
};

function makeProposal(scenario: string): ProposalDetail {
  return {
    proposal_id: "00000000-0000-4000-8000-000000000005",
    state: "ready",
    lifecycle_state: "ready",
    support_mode: "context_only",
    upstream_revision: "6045f004fbdc4887c2bb67da9ae19a571314fc27",
    trip_handle: preview.trip_handle,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + (scenario === "expiry" ? 4000 : 60_000)).toISOString(),
    base_trip_revision: 4,
    current_trip_revision: 4,
    base_place_revisions: [],
    current_place_revisions: [],
    operations: [{
      kind: "add_item",
      day_handle: "h_fixture_day_0001",
      candidate_handle: "h_fixture_cand_0001",
      item_type: "activity",
      position: 0,
      start_time: "10:00",
      end_time: "11:00",
    }],
    operation_support: [],
    citations: [],
    preview,
    applied_outcome: null,
    failure_code: null,
  };
}

function appliedOutcome(proposal: ProposalDetail): ProposalApplyOutcome {
  return {
    proposal_id: proposal.proposal_id,
    state: "applied",
    applied_revision: 5,
    applied_at: new Date().toISOString(),
    preview: proposal.preview!,
  };
}

export default function ProposalLifecycleFixture() {
  const [scenario, setScenario] = useState("expiry");
  const [workspaceBlocked, setWorkspaceBlocked] = useState(false);

  useEffect(() => {
    let detailReads = 0;
    const saved = {
      createProposal: travelApi.createProposal,
      getProposal: travelApi.getProposal,
      getProposalByKey: travelApi.getProposalByKey,
      applyProposal: travelApi.applyProposal,
      rejectProposal: travelApi.rejectProposal,
    };

    travelApi.createProposal = async () => makeProposal(scenario);
    travelApi.getProposal = async () => {
      const value = makeProposal(scenario);
      detailReads += 1;
      if (scenario === "stale") {
        return { ...value, state: "stale", current_trip_revision: 5 };
      }
      if (scenario === "lost-apply" && detailReads > 1) {
        return {
          ...value,
          state: "applied",
          lifecycle_state: "applied",
          applied_outcome: appliedOutcome(value),
        };
      }
      return value;
    };
    travelApi.getProposalByKey = async () => makeProposal(scenario);
    travelApi.applyProposal = async () => {
      if (scenario === "lost-apply") throw new Error("fixture lost the committed response");
      return appliedOutcome(makeProposal(scenario));
    };
    travelApi.rejectProposal = async () => ({
      ...makeProposal(scenario),
      state: "rejected",
      lifecycle_state: "rejected",
    });

    return () => {
      Object.assign(travelApi, saved);
    };
  }, [scenario]);

  return (
    <main className="pageShell" data-testid="proposal-lifecycle-fixture">
      <p className="eyebrow">LOCAL BROWSER FIXTURE</p>
      <h1>Proposal lifecycle checks</h1>
      <label>
        Scenario
        <select
          aria-label="Scenario"
          value={scenario}
          onChange={(event) => {
            setScenario(event.target.value);
            setWorkspaceBlocked(false);
          }}
        >
          <option value="expiry">Expiry while preview is open</option>
          <option value="stale">Stale before apply</option>
          <option value="lost-apply">Apply response lost, refresh fails</option>
        </select>
      </label>
      {workspaceBlocked ? (
        <p role="status" data-testid="workspace-reload-required">
          Workspace reload required before editing.
        </p>
      ) : null}
      <ProposalPanel
        trip={trip}
        workspacePlaceFootprint=""
        busy={false}
        disabled={workspaceBlocked}
        onCommitted={async () => {
          setWorkspaceBlocked(true);
          return false;
        }}
        onPendingChange={() => undefined}
      />
    </main>
  );
}
