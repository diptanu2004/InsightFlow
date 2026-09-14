import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useDatasets, useOrganization, useProject } from '../api/queries'
import { ROLE_LABELS, hasAtLeastRole } from '../auth/roles'
import DatasetUpload from '../components/DatasetUpload'
import ChatPanel from '../components/chat/ChatPanel'
import DashboardSection from '../components/dashboard/DashboardSection'
import SemanticModelView from '../components/SemanticModelView'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const project = useProject(projectId)
  const organization = useOrganization(project.data?.org_id)
  const datasets = useDatasets(projectId)

  const canUpload = organization.data ? hasAtLeastRole(organization.data.my_role, 'analyst') : false
  // Newest first from the route, and that's the one every query/dashboard/chat request resolves
  // to server-side -- a re-upload creates a new Dataset row rather than mutating this one.
  const activeDataset = datasets.data?.[0]

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

      <section className="mt-8">
        <h2 className="text-sm font-semibold text-slate-900">Data</h2>

        {datasets.isPending && <p className="mt-2 text-sm text-slate-500">Loading…</p>}

        {datasets.isError && (
          <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            Couldn't load datasets — {datasets.error.message}
          </p>
        )}

        {datasets.data?.length === 0 && (
          <p className="mt-2 text-sm text-slate-500">
            {canUpload
              ? 'No data yet. Upload CSVs to build a semantic model.'
              : 'No data yet. An Analyst can upload CSVs for this project.'}
          </p>
        )}

        {canUpload && projectId && (
          <div className="mt-3">
            <DatasetUpload projectId={projectId} />
          </div>
        )}

        {activeDataset && (
          <div className="mt-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-sm font-medium text-slate-900">{activeDataset.name}</h3>
              <span className="text-xs text-slate-500">
                saved {new Date(activeDataset.created_at).toLocaleString()}
                {datasets.data && datasets.data.length > 1 && (
                  <> · {datasets.data.length - 1} earlier version{datasets.data.length === 2 ? '' : 's'}</>
                )}
              </span>
            </div>
            <div className="mt-3">
              <SemanticModelView
                // Keyed by dataset id: saving a review creates a new version, and remounting resets
                // the staged decisions that belonged to the version they were made against.
                key={activeDataset.id}
                model={activeDataset.semantic_model}
                projectId={activeDataset.project_id}
                datasetId={activeDataset.id}
                canReview={canUpload}
              />
            </div>
          </div>
        )}
      </section>

      {/* Sibling keys must differ: the same bare dataset id on both made React warn about duplicate keys
          (caught by the M6 end-to-end run), and duplicates can leave one of them un-remounted. */}
      {activeDataset && projectId && <DashboardSection key={`dashboard-${activeDataset.id}`} projectId={projectId} />}

      {activeDataset && projectId && <ChatPanel key={`chat-${activeDataset.id}`} projectId={projectId} />}
    </div>
  )
}
