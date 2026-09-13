import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useCreateOrganization, useOrganizations } from '../api/queries'
import { ROLE_LABELS } from '../auth/roles'

export default function OrganizationsPage() {
  const organizations = useOrganizations()
  const createOrganization = useCreateOrganization()
  const [name, setName] = useState('')

  async function onCreate(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    await createOrganization.mutateAsync(trimmed)
    setName('')
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-slate-900">Organizations</h1>
      <p className="mt-1 text-sm text-slate-500">
        Every dataset, dashboard and question lives inside a project, and every project belongs to
        an organization.
      </p>

      {organizations.isPending && <p className="mt-6 text-sm text-slate-500">Loading…</p>}

      {organizations.isError && (
        <p role="alert" className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          Couldn't load organizations — {organizations.error.message}
        </p>
      )}

      {organizations.data && (
        <ul className="mt-6 space-y-2">
          {organizations.data.map((org) => (
            <li key={org.id}>
              <Link
                to={`/orgs/${org.id}`}
                className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3 hover:border-slate-400"
              >
                <span>
                  <span className="text-sm font-medium text-slate-900">{org.name}</span>
                  <span className="ml-2 text-xs text-slate-400">{org.slug}</span>
                </span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                  {ROLE_LABELS[org.my_role]}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {organizations.data?.length === 0 && (
        <p className="mt-6 rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500">
          You're not a member of any organization yet. Create one to get started.
        </p>
      )}

      <form onSubmit={onCreate} className="mt-8 rounded-lg border border-slate-200 bg-white p-4">
        <label htmlFor="org-name" className="block text-sm font-medium text-slate-700">
          New organization
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="org-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Retail"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
          />
          <button
            type="submit"
            disabled={createOrganization.isPending || !name.trim()}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {createOrganization.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">You'll be its Owner.</p>
        {createOrganization.isError && (
          <p role="alert" className="mt-2 text-sm text-red-700">
            {createOrganization.error.message.replace(/^HTTP \d+: /, '')}
          </p>
        )}
      </form>
    </div>
  )
}
