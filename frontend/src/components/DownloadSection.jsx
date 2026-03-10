// Matches the ORM table names and call statuses defined in the backend
const DISPOSITIONS = ['All', 'completed', 'failed', 'timeout', 'calling', 'active'];
const TABLE_NAMES = [
  { label: 'Customer Master', value: 'customer' },
  { label: 'Call History', value: 'call_metadata' },
  { label: 'Chat Transcripts', value: 'conversation' },
  { label: 'Feedback Data', value: 'customer_feedback' }
];

const selectCls =
  'w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 ' +
  'focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent transition ' +
  'appearance-none cursor-pointer'

export default function DownloadSection({ filters, setFilters, onDownload, compact = false }) {
  const canDownload = true // In real-time, this is always ready as the DB is always live

  return (
    <div className={compact ? '' : 'bg-white rounded-2xl border border-gray-200 p-6 shadow-sm flex flex-col'}>
      {!compact && (
        <div className="mb-4">
          <h3 className="font-semibold text-gray-900 mb-1">Download Excel</h3>
          <p className="text-sm text-gray-500">Export filtered data from the ORM database</p>
        </div>
      )}

      <div className="space-y-3">
        {/* Disposition filter: Affects call_metadata and feedback status */}
        <div>
          <label className="block text-[10px] uppercase font-bold text-gray-400 mb-1 tracking-wider">
            Disposition
          </label>
          <div className="relative">
            <select
              value={filters.disposition}
              onChange={e => setFilters(prev => ({ ...prev, disposition: e.target.value }))}
              className={selectCls}
            >
              {DISPOSITIONS.map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 text-xs">▼</span>
          </div>
        </div>

        {/* Table name filter: Maps directly to SQLAlchemy __tablename__ */}
        <div>
          <label className="block text-[10px] uppercase font-bold text-gray-400 mb-1 tracking-wider">
            Data Source
          </label>
          <div className="relative">
            <select
              value={filters.tableName}
              onChange={e => setFilters(prev => ({ ...prev, tableName: e.target.value }))}
              className={selectCls}
            >
              {TABLE_NAMES.map(t => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 text-xs">▼</span>
          </div>
        </div>
      </div>

      <button
        onClick={onDownload}
        disabled={!canDownload}
        className={
          'mt-4 w-full py-2.5 rounded-xl font-semibold text-sm transition-all ' +
          (canDownload
            ? 'bg-gray-900 text-white hover:bg-gray-700 active:scale-[0.98] shadow-md'
            : 'bg-gray-100 text-gray-400 cursor-not-allowed')
        }
      >
        {'\u2193\uFE0E'} Export to Excel
      </button>
    </div>
  )
}