/**
 * TanStack Query hooks over the tenancy routes. One place per route so cache keys and
 * invalidation stay consistent.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { postJson, request } from './client'
import type { OrganizationOut, ProjectOut } from './types'

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
