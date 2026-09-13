import type { Role } from '../api/types'

/**
 * Mirrors `auth/rbac.py`'s `_ROLE_RANK`.
 *
 * This only decides what the UI *offers* -- every route re-checks the same ordering server-side,
 * so a stale copy here can hide a control the user is actually entitled to, but can never grant
 * access. Because `Role` is generated from the backend enum, adding a role there makes this
 * record fail to compile rather than silently ranking the new role as 0.
 */
const RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2, owner: 3 }

export function hasAtLeastRole(mine: Role, required: Role): boolean {
  return RANK[mine] >= RANK[required]
}

export const ROLE_LABELS: Record<Role, string> = {
  viewer: 'Viewer',
  analyst: 'Analyst',
  admin: 'Admin',
  owner: 'Owner',
}
