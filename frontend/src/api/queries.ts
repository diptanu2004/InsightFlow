/**
 * TanStack Query hooks over the tenancy routes. One place per route so cache keys and
 * invalidation stay consistent.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { postForm, postJson, request } from './client'
import type { Answer, DatasetOut, HydratedDashboard, JobOut, MappingDecision, OrganizationOut, ProjectOut } from './types'

const ORGANIZATIONS_KEY = ['organizations']

function fetchOrganizations() {
  return request<OrganizationOut[]>('/organizations')
}

export function useOrganizations() {
  return useQuery({ queryKey: ORGANIZATIONS_KEY, queryFn: fetchOrganizations })
}

/**
 * There's no `GET /organizations/{id}` route, so a single org (its name, and crucially the
 * caller's `my_role` in it) is derived from the list -- same query key, so both views share one
 * cache entry and one request.
 */
export function useOrganization(orgId: string | undefined) {
  return useQuery({
    queryKey: ORGANIZATIONS_KEY,
    queryFn: fetchOrganizations,
    enabled: orgId !== undefined,
    select: (orgs) => orgs.find((org) => org.id === orgId) ?? null,
  })
}

export function useCreateOrganization() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => postJson<OrganizationOut>('/organizations', { name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ORGANIZATIONS_KEY }),
  })
}

export function useProjects(orgId: string | undefined) {
  return useQuery({
    queryKey: ['projects', orgId],
    queryFn: () => request<ProjectOut[]>(`/organizations/${orgId}/projects`),
    enabled: orgId !== undefined,
  })
}

export function useCreateProject(orgId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => postJson<ProjectOut>(`/organizations/${orgId}/projects`, { name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects', orgId] }),
  })
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: ['project', projectId],
    queryFn: () => request<ProjectOut>(`/projects/${projectId}`),
    enabled: projectId !== undefined,
  })
}

/** Newest first, so `[0]` is the dataset query/dashboard/chat actually run against. */
export function useDatasets(projectId: string | undefined) {
  return useQuery({
    queryKey: ['datasets', projectId],
    queryFn: () => request<DatasetOut[]>(`/projects/${projectId}/datasets`),
    enabled: projectId !== undefined,
  })
}

export function useUploadDataset(projectId: string | undefined) {
  return useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData()
      for (const file of files) form.append('files', file)
      // Returns 202 + a job id, not a dataset -- discovery is LLM-heavy and runs on an RQ
      // worker (Phase 7 M3). Poll with `useDiscoveryJob`.
      return postForm<JobOut>(`/projects/${projectId}/schema/discover`, form)
    },
  })
}

/**
 * Submits one batch of confirm/reject decisions. The backend answers with a NEW dataset version (the
 * old row is never edited -- result caches rely on that), which becomes the project's active dataset.
 */
export function useReviewMappings(projectId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ datasetId, decisions }: { datasetId: string; decisions: MappingDecision[] }) =>
      postJson<DatasetOut>(`/projects/${projectId}/datasets/${datasetId}/mapping-decisions`, { decisions }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['datasets', projectId] }),
  })
}

/**
 * A mutation, not a query: generating spends an LLM planning call, so it runs only when someone
 * asks, never on mount or window focus. Dashboards are ephemeral in v1 (no dashboards table); the
 * backend's result cache makes asking again for the same dataset version free.
 */
export function useGenerateDashboard(projectId: string | undefined) {
  return useMutation({
    mutationFn: () => postJson<HydratedDashboard>(`/projects/${projectId}/dashboard/generate`, {}),
  })
}

/** One question, one answer: the backend keeps no conversation, so each question stands alone. */
export function useAskQuestion(projectId: string | undefined) {
  return useMutation({
    mutationFn: (question: string) => postJson<Answer>(`/projects/${projectId}/chat/ask`, { question }),
  })
}

/** Terminal job states -- polling stops here rather than hammering the route forever. */
const TERMINAL_JOB_STATUSES = new Set<JobOut['status']>(['done', 'failed'])

export function useDiscoveryJob(projectId: string | undefined, jobId: string | null) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: ['discoveryJob', projectId, jobId],
    queryFn: async () => {
      const job = await request<JobOut>(`/projects/${projectId}/schema/jobs/${jobId}`)
      // The new dataset becomes "the" dataset for the project the moment discovery finishes.
      if (job.status === 'done') await queryClient.invalidateQueries({ queryKey: ['datasets', projectId] })
      return job
    },
    enabled: jobId !== null && projectId !== undefined,
    refetchInterval: (query) =>
      query.state.data && TERMINAL_JOB_STATUSES.has(query.state.data.status) ? false : 1500,
  })
}
