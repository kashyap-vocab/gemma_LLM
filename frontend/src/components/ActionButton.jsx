/**
 * Call action button — status-aware, drives per-row call lifecycle.
 * Core statuses: pending | calling | active | completed | incomplete | missedcall
 */
export default function ActionButton({ status, onClick }) {

  // TERMINAL STATE: completed
  if (status === 'completed') {
    return (
      <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 text-xs font-bold border border-emerald-200 select-none shadow-sm">
        ✓ Done
      </span>
    );
  }

  // RINGING STATE: calling (SmartFlo triggered, waiting for answer)
  if (status === 'calling') {
    return (
      <button
        disabled
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-yellow-50 text-yellow-700 border border-yellow-200 text-xs font-bold cursor-not-allowed animate-pulse select-none"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 inline-block" />
        Calling...
      </button>
    );
  }

  // TALKING STATE: active (customer answered)
  if (['active', 'agent_ready'].includes(status)) {
    return (
      <button
        disabled
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-50 text-purple-600 border border-purple-200 text-xs font-bold cursor-not-allowed animate-pulse select-none"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-purple-500 inline-block" />
        Talking...
      </button>
    );
  }

  // INCOMPLETE STATE: connected but survey not finished
  if (status === 'incomplete') {
    return (
      <button
        onClick={onClick}
        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-orange-50 text-orange-700 text-xs font-bold border border-orange-200 hover:bg-orange-100 transition-all active:scale-95"
      >
        ↻ Retry
      </button>
    );
  }

  // MISSED / FAILED STATE: retry
  if (['missedcall', 'failed', 'timeout'].includes(status)) {
    return (
      <button
        onClick={onClick}
        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold border border-slate-200 hover:bg-slate-200 transition-all active:scale-95"
      >
        ↻ Retry
      </button>
    );
  }

  // DISABLED STATE
  if (status === 'disabled') {
    return (
      <button
        onClick={onClick}
        className="inline-flex items-center px-3 py-1.5 rounded-lg text-xs font-bold border border-gray-300 text-gray-600 bg-white hover:bg-gray-50 hover:border-gray-400 transition-colors"
      >
        Enable
      </button>
    );
  }

  // DEFAULT STATE: pending → Call
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-900 text-white text-xs font-bold hover:bg-gray-700 active:bg-gray-800 transition-all active:scale-95 shadow-md shadow-gray-200"
    >
      <span className="text-[10px]">▶</span> Call
    </button>
  );
}
