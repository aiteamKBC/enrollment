import { MemoryRouter } from 'react-router-dom';
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { id: 114 } } }) }));
import type { ReactNode } from 'react';
vi.mock('@/features/programme-suitability/api', () => ({ prefetchAssessmentSecurity: vi.fn(async () => ({csrfToken:'synthetic-csrf-token'})) }));
import { beforeEach, expect, it, vi } from "vitest";
import { render as renderWithoutRouter, screen } from "@testing-library/react";
import ProgrammeStructurePage from "../programme-structure/page";
vi.mock("@/pages/admin/_shared/AdminPage", () => ({ AdminPage: ({ children, actions }: any) => <div>{actions}{children}</div>, DataPanel: ({ children, error, loading }: any) => loading ? <div>loading</div> : error ? <div>{error}</div> : <div>{children}</div>, StatusBadge: ({ status }: any) => <span>{status}</span> }));

vi.mock("@/api/wizardLayout", () => ({ fetchWizardLayout: vi.fn(async () => ({ version: 4, layout: { programmeModules: { programmes: [{ id: "ME_L4", title: "Marketing Executive", modules: [{ id: "m1", title: "Impact", active: true, selectable: true, isMandatory: true }] }], }, skillsRadarCatalogues: { ME_L4: { records: [{ id: "ksb-1", code: "K1", kind: "Knowledge", description: "One", moduleKey: "m1" }] } } } })) }));

beforeEach(() => { vi.restoreAllMocks(); vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => { if (url.includes("/draft/") && init?.method === "PUT") return new Response(JSON.stringify({structure: JSON.parse(String(init.body)).structure, baseVersion:4, revision:1}),{status:200}); if (url.includes("/draft/")) return new Response(JSON.stringify({ structure: null, revision: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }); if (url.includes("/preview/")) return new Response(JSON.stringify({ valid: false, version: "draft-v1", unsupportedProgrammes: [], blockers: ["PostgreSQL verification pending"] }), { status: 200 }); return new Response("{}", { status: 200 }); })); });

it("keeps legacy structure internal when no managed Cohort exists", async () => { render(<ProgrammeStructurePage />); const card = await screen.findByText("Marketing Executive"); expect(card).toBeVisible(); const {default:userEvent}=await import("@testing-library/user-event"); await userEvent.setup().click(card); expect(screen.getByText(/No managed Cohorts yet. Use Add Cohort/)).toBeVisible(); expect(screen.queryByText("K1")).not.toBeInTheDocument(); expect(screen.getByRole('button',{name:'Add Cohort'})).toBeEnabled(); });

it("does not show a Programme Active or Inactive status badge", async () => { render(<ProgrammeStructurePage />); expect(await screen.findByText("Marketing Executive")).toBeVisible(); expect(screen.queryByText("active", { exact: true })).not.toBeInTheDocument(); expect(screen.queryByText("archived", { exact: true })).not.toBeInTheDocument(); });

it("removes standalone Add KSB creation from Programme Structure", async () => { render(<ProgrammeStructurePage />); expect(await screen.findByText("Marketing Executive")).toBeVisible(); expect(screen.queryByRole("button", { name: "Add KSB" })).not.toBeInTheDocument(); });

it("loads a read-only publish preview and keeps Publish disabled", async () => { render(<ProgrammeStructurePage />); const card = await screen.findByText("Marketing Executive"); const user = await import("@testing-library/user-event").then(({default: userEvent}) => userEvent.setup()); await user.click(card); await user.click(screen.getByRole("button", { name: "Draft Changes" })); await user.click(screen.getByRole("button", { name: "Publish Preview" })); expect(await screen.findByRole("region", { name: "Publish Preview" })).toBeVisible(); expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled(); expect(screen.getByText(/Publishing is currently unavailable/)).toBeVisible(); });

it("keeps assignment period editing out of the normal page and Add Cohort",async()=>{
 render(<ProgrammeStructurePage/>);
 const user=await import('@testing-library/user-event').then(({default:userEvent})=>userEvent.setup());
 await user.click(await screen.findByText('Marketing Executive'));
 expect(screen.queryByRole('button',{name:'Cohort Calendar'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('Start Day')).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Add Cohort'}));
 expect(screen.getByLabelText('Create Cohort From')).toBeVisible();
 expect(screen.queryByLabelText('Start Month')).not.toBeInTheDocument();
 expect(screen.queryByLabelText('End Day')).not.toBeInTheDocument();
});

function render(ui: ReactNode) { return renderWithoutRouter(<MemoryRouter>{ui}</MemoryRouter>); }
