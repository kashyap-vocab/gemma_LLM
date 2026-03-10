import { useRef, useState } from 'react'
import DateRangePicker from './DateRangePicker'
import DownloadSection from './DownloadSection'

export default function InitialView({ dateRange, setDateRange, filters, setFilters, onUpload, onDownload }) {
  const fileInputRef = useRef()
  const [isUploading, setIsUploading] = useState(false)

  const handleFileChange = async (e) => {
    const file = e.target.files[0]
    if (!file) return

    setIsUploading(true)
    try {
      // Trigger the upload function passed from App.js
      // This will handle the fetch to /api/customers/upload
      await onUpload(file)
    } catch (error) {
      console.error("Upload failed:", error)
      alert("Failed to upload file. Please try again.")
    } finally {
      setIsUploading(false)
      e.target.value = '' // Reset input
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* ── Top bar ─────────────────────────────────── */}
      <header className="bg-white border-b border-gray-100 px-6 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-gray-900 rounded-lg flex items-center justify-center shrink-0">
            <span className="text-white text-xs font-bold">LT</span>
          </div>
          <span className="font-semibold text-gray-900 text-sm">LTFS Call Manager</span>
        </div>
      </header>

      {/* ── Centered content ────────────────────────── */}
      <div className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        {/* Hero */}
        <div className="text-center mb-10">
          <div className="w-14 h-14 bg-gray-900 rounded-2xl mx-auto mb-5 flex items-center justify-center shadow-lg">
            <span className="text-2xl">📞</span>
          </div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2 tracking-tight">
            LTFS Call Manager
          </h1>
          <p className="text-gray-500 text-base">
            Manage and monitor your customer call campaigns
          </p>
        </div>

        {/* Date range — shared by both features */}
        <div className="w-full max-w-2xl mb-5">
          <DateRangePicker dateRange={dateRange} setDateRange={setDateRange} />
        </div>

        {/* Two feature cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 w-full max-w-2xl">
          
          {/* Upload card */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 shadow-sm flex flex-col">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg">📤</span>
              <h3 className="font-semibold text-gray-900">Upload Excel</h3>
            </div>
            <p className="text-sm text-gray-500 mb-5 leading-relaxed">
              Import your customer list from an Excel file to start a call campaign.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              onChange={handleFileChange}
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current.click()}
              disabled={isUploading}
              className={`mt-auto w-full py-2.5 rounded-xl font-medium text-sm transition-colors ${
                isUploading 
                  ? 'bg-gray-400 cursor-not-allowed' 
                  : 'bg-gray-900 text-white hover:bg-gray-700 active:bg-gray-800'
              }`}
            >
              {isUploading ? 'Uploading...' : 'Choose File'}
            </button>
          </div>

          {/* Download card */}
          <DownloadSection
            filters={filters}
            setFilters={setFilters}
            // onDownload will trigger window.open with params in App.js
            onDownload={onDownload}
          />
        </div>

        {/* Subtle footer hint */}
        <p className="mt-10 text-xs text-gray-400 text-center">
          Upload an Excel file to view and manage call actions &nbsp;·&nbsp; Set filters to export data
        </p>
      </div>
    </div>
  )
}