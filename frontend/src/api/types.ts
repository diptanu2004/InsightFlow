/**
 * Ergonomic aliases over the generated schema, so application code never writes
 * `components['schemas'][...]` by hand.
 *
 * Everything here is generated from the backend's OpenAPI document -- run `npm run gen:api` after
 * any backend model change. Do not hand-edit schema.d.ts or add types here that the backend
 * doesn't actually serve; a hand-written type that drifts from the real payload is exactly the
 * failure this pipeline exists to prevent.
 */
import type { components } from './schema'

type S = components['schemas']

// -- insightflow_core: the analytics engine's query AST and result shape --
export type AnalyticalQuery = S['AnalyticalQuery']
export type MetricResult = S['MetricResult']
export type QueryMetadata = S['QueryMetadata']
export type OperationType = S['OperationType']
export type TimeFilter = S['TimeFilter']
export type GrowthSpec = S['GrowthSpec']
export type HavingClause = S['HavingClause']
export type SortSpec = S['SortSpec']

// -- POC 3: dashboard spec + hydrated output --
export type HydratedDashboard = S['HydratedDashboard']
export type HydratedComponent = S['HydratedComponent']
export type ComponentSpec = S['ComponentSpec']
export type ComponentType = S['ComponentType']
export type FilterSpec = S['FilterSpec']

// -- POC 4: NL chatbot --
export type Answer = S['Answer']
export type QuestionResult = S['QuestionResult']
export type QuestionIntent = S['QuestionIntent']
export type QuestionOperation = S['QuestionOperation']
export type CategoryDelta = S['CategoryDelta']
export type TimeExpression = S['TimeExpression']

// -- Phase 6/7: tenancy, datasets, async jobs --
export type OrganizationOut = S['OrganizationOut']
export type ProjectOut = S['ProjectOut']
export type MemberOut = S['MemberOut']
export type DatasetOut = S['DatasetOut']
export type JobOut = S['JobOut']
export type JobStatus = S['JobStatus']
export type Role = S['Role']
export type TokenPair = S['TokenPair']
export type MeOut = S['MeOut']
