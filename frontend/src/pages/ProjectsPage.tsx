import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { useCreateProject, useOrganization, useProjects } from '../api/queries'
import { ROLE_LABELS, hasAtLeastRole } from '../auth/roles'

export default function ProjectsPage() {
  const { orgId } = useParams<{ orgId: string }>()
  const organization = useOrganization(orgId)
  const projects = useProjects(orgId)
  const createProject = useCreateProject(orgId)
  const [name, setName] = useState('')

  const canCreate = organization.data ? hasAtLeastRole(organization.data.my_role, 'admin') : false

  async function onCreate(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    await createProject.mutateAsync(trimmed)
    setName('')
  }

  // `useOrganization` derives from the org list, so a resolved-but-null org means the user isn't
  // a member of it (or the id is wrong) -- not a loading state.
  if (organization.isSuccess && organization.data === null) {
    return (
      <div>
        <Link to="/" className="text-sm text-slate-500 hover:text-slate-900">
          ← Organizations
        </Link>
        <p role="alert" className="mt-6 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          That organization doesn't exist, or you're not a member of it.
        </p>
      </div>
    )
  }

  return (
    <div>
      <Link to="/" className="text-sm text-slate-500 hover:text-slate-900">
        ← Organizations
      </Link>

      <div className="mt-3 flex items-baseline gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          {organization.data?.name ?? 'Projects'}
        </h1>
        {organization.data && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
            {ROLE_LABELS[organization.data.my_role]}
          </span>
        )}
      </div>

      {projects.isPending && <p className="mt-6 text-sm text-slate-500">Loading…</p>}

      {projects.isError && (
        <p role="alert" className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          Couldn't load projects — {projects.error.message}
        </p>
      )}

      {projects.data && projects.data.length > 0 && (
        <ul className="mt-6 space-y-2">
          {projects.data.map((project) => (
            <li key={project.id}>
              <Link
                to={`/projects/${project.id}`}
                className="block rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-900 hover:border-slate-400"
              >
                {project.name}
              </Link>
            </li>
          ))}
        </ul>
      )}

      {projects.data?.length === 0 && (
        <p className="mt-6 rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500">
          No projects yet.{!canCreate && ' An Admin can create one.'}
        </p>
      )}

      {canCreate && (
        <form onSubmit={onCreate} className="mt-8 rounded-lg border border-slate-200 bg-white p-4">
          <label htmlFor="project-name" className="block text-sm font-medium text-slate-700">
            New project
          </label>
          <div className="mt-2 flex gap-2">
            <input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Q3 sales analysis"
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
            />
            <button
              type="submit"
              disabled={createProject.isPending || !name.trim()}
              className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {createProject.isPending ? 'Creating…' : 'Create'}
            </button>
          </div>
          {createProject.isError && (
            <p role="alert" className="mt-2 text-sm text-red-700">
              {createProject.error.message.replace(/^HTTP \d+: /, '')}
            </p>
          )}
        </form>
      )}
    </div>
  )
}
