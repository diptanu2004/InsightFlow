import type { SemanticModel } from '../api/types'

/**
 * Mirrors POC 1's `CONFIDENCE_THRESHOLD` default (see
 * poc1_schema_discovery/src/insightflow_schema_discovery/config.py). POC 1's `SemanticMapper`
 * already computes `needs_confirmation = confidence < threshold`, but that flag lives on its own
 * internal mapping type and is dropped at the `SemanticModel` boundary -- `SemanticField` carries
 * only the raw confidence -- so the value has to be re-derived here. Note the backend reads that
 * threshold from an env var, so a deployment that changes it would disagree with this copy.
 */
const CONFIDENCE_THRESHOLD = 0.75

function ConfidenceBadge({ value }: { value: number }) {
  const low = value < CONFIDENCE_THRESHOLD
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
        low ? 'bg-amber-100 text-amber-800' : 'bg-emerald-50 text-emerald-700'
      }`}
      title={low ? 'Below the confidence threshold — worth confirming' : 'High confidence'}
    >
      {Math.round(value * 100)}%
    </span>
  )
}

export default function SemanticModelView({ model }: { model: SemanticModel }) {
  const fieldsNeedingReview = model.entities.flatMap((entity) =>
    entity.fields.filter((field) => field.confidence < CONFIDENCE_THRESHOLD),
  )

  return (
    <div className="space-y-6">
      {fieldsNeedingReview.length > 0 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {fieldsNeedingReview.length} column{fieldsNeedingReview.length === 1 ? '' : 's'} were
          mapped below the {Math.round(CONFIDENCE_THRESHOLD * 100)}% confidence threshold. Analytics
          still run, but these are the mappings worth checking first.
        </p>
      )}

      <section>
        <h3 className="text-sm font-medium text-slate-700">
          Entities <span className="font-normal text-slate-400">({model.entities.length})</span>
        </h3>
        <div className="mt-3 space-y-4">
          {model.entities.map((entity) => (
            <div key={entity.name} className="overflow-hidden rounded-lg border border-slate-200">
              <div className="flex items-baseline justify-between bg-slate-50 px-4 py-2">
                <span className="text-sm font-medium text-slate-900">{entity.name}</span>
                <span className="text-xs text-slate-500">{entity.fields.length} fields</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                      <th className="px-4 py-2 font-medium">Semantic field</th>
                      <th className="px-4 py-2 font-medium">Source column</th>
                      <th className="px-4 py-2 font-medium">File</th>
                      <th className="px-4 py-2 font-medium">Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entity.fields.map((field) => (
                      <tr key={`${field.source_file}.${field.source_column}`} className="border-b border-slate-100 last:border-0">
                        <td className="px-4 py-2 font-medium text-slate-800">{field.name}</td>
                        <td className="px-4 py-2 font-mono text-xs text-slate-600">{field.source_column}</td>
                        <td className="px-4 py-2 text-xs text-slate-500">{field.source_file}</td>
                        <td className="px-4 py-2">
                          <ConfidenceBadge value={field.confidence} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium text-slate-700">
          Relationships{' '}
          <span className="font-normal text-slate-400">({model.relationships.length})</span>
        </h3>
        {model.relationships.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No cross-file relationships were detected.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {model.relationships.map((relationship) => (
              <li
                key={`${relationship.from_field}->${relationship.to_field}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-200 px-4 py-2 text-sm"
              >
                <span className="font-mono text-xs text-slate-700">{relationship.from_field}</span>
                <span className="text-slate-400">→</span>
                <span className="font-mono text-xs text-slate-700">{relationship.to_field}</span>
                {/* `direction` is deliberately not rendered: POC 1 builds it as
                    f"{from_field} -> {to_field}" (relationship_detector.py), so it restates the
                    two fields above by construction and never carries a cardinality. */}
                <ConfidenceBadge value={relationship.confidence} />
                {!relationship.validated && (
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                    unvalidated
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
