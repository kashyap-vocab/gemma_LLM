import DateRangePicker from './DateRangePicker'
import DownloadSection from './DownloadSection'

export default function Sidebar({ 
  dateRange, 
  setDateRange, 
  filters, 
  setFilters, 
  onDownload 
}) {
  return (
    <aside className="w-72 shrink-0 bg-white border-r border-gray-100 flex flex-col h-full overflow-y-auto scrollbar-thin">
      <div className="p-5 space-y-4 flex-1">
        {/* Section label */}
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Filters
        </p>

        {/* DATE RANGE: 
            In the real API flow, changing these dates in App.js should 
            trigger a re-fetch of the /api/customers list filtered by uploaded_at.
        */}
        <DateRangePicker
          dateRange={dateRange}
          setDateRange={setDateRange}
          compact
        />

        {/* Divider */}
        <div className="border-t border-gray-100" />

        {/* Export Section */}
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Export
        </p>
        
        {/* DOWNLOAD SECTION:
            The 'filters' state here is critical. When 'onDownload' is clicked, 
            it uses filters.tableName (customer_feedback or conversation) 
            and filters.disposition to hit the dynamic ORM export endpoint.
        */}
        <DownloadSection
          filters={filters}
          setFilters={setFilters}
          onDownload={onDownload}
          compact
        />
      </div>
      
      {/* Optional: Add a subtle 'Sync' indicator at the bottom */}
      <div className="p-4 bg-gray-50 border-t border-gray-100">
         <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            <span className="text-[10px] text-gray-500 font-medium uppercase tracking-tight">
               Connected to ORM Database
            </span>
         </div>
      </div>
    </aside>
  )
}