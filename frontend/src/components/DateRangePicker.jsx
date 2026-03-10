export default function DateRangePicker({ dateRange, setDateRange, compact = false }) {
  const labelCls = compact
    ? 'text-[10px] font-bold text-gray-400 uppercase tracking-tight mb-1'
    : 'text-sm font-medium text-gray-600 mb-1.5'

  const inputCls =
    'w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 ' +
    'focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent transition ' +
    'cursor-pointer hover:border-gray-300'

  return (
    <div className={`bg-white rounded-2xl border border-gray-200 p-4 shadow-sm ${compact ? 'border-none p-0 shadow-none' : ''}`}>
      {!compact && (
        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-3">
          Filter by Upload Date
        </p>
      )}
      
      <div className={compact ? 'flex flex-col gap-3' : 'flex items-center gap-3'}>
        {/* Start Date */}
        <div className="flex-1">
          <p className={labelCls}>Start Date</p>
          <input
            type="date"
            value={dateRange.start}
            onChange={e => setDateRange(prev => ({ ...prev, start: e.target.value }))}
            className={inputCls}
          />
        </div>

        {/* Separator */}
        {!compact && (
          <div className="mt-6 text-gray-300 text-lg select-none">→</div>
        )}

        {/* End Date */}
        <div className="flex-1">
          <p className={labelCls}>End Date</p>
          <input
            type="date"
            value={dateRange.end}
            min={dateRange.start} // Prevents end date being before start date
            onChange={e => setDateRange(prev => ({ ...prev, end: e.target.value }))}
            className={inputCls}
          />
        </div>
      </div>
    </div>
  )
}