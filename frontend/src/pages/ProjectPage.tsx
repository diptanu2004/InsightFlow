import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useOrganization, useProject } from '../api/queries'
import { ROLE_LABELS } from '../auth/roles'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const project = useProject(projectId)
  const organization = useOrganization(project.data?.org_id)

  if (project.isError) {
    const status = project.error instanceof ApiError ? project.error.status : null
    return (
      <div>
        <Link to="/" className="text-sm text-slate-500 hover:text-slate-900">
          ← Organizations
        </Link>
        <p role="alert" className="mt-6 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {status === 404
            ? "That project doesn't exist."
            : status === 403
              ? "You don't have access to that project."
              : `Couldn't load the project — ${project.error.message}`}
        </p>
      </div>
    )
  }

  return (
    <div>
      <Link
        to={project.data ? `/orgs/${project.data.org_id}` : '/'}
        className="text-sm text-slate-500 hover:text-slate-900"
      >
        ← {organization.data?.name ?? 'Back'}
      </Link>

      <div className="mt-3 flex items-baseline gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          {project.isPending ? 'Loading…' : project.data?.name}
        </h1>
        {organization.data && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
            {ROLE_LABELS[organization.data.my_role]}
          </span>
        )}
      </div>

      <div className="mt-8 space-y-3">
        {[
          ['Data', 'Upload CSVs and review the inferred semantic model — M3.'],
          ['Dashboard', 'Auto-generated dashboard from the semantic model — M4.'],
          ['Ask', 'Natural-language questions with verified numbers — M5.'],
        ].map(([title, description]) => (
          <div key={title} className="rounded-lg border border-dashed border-slate-300 px-4 py-5">
            <h2 className="text-sm font-medium text-slate-700">{title}</h2>
            <p className="mt-1 text-sm text-slate-500">{description}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
