import ActionButton from './ActionButton';

/**
 * STATUS_MAP: Matches call_status values from the backend ORM.
 * Core values: pending | active | completed | missedcall
 * Legacy values kept for backward-compat with older DB rows.
 */
const STATUS_MAP = {
  // ── Current status values ─────────────────────────────────────────────────
  pending:    { label: 'Pending',         cls: 'bg-amber-50 text-amber-700 border-amber-100' },
  calling:    { label: 'Calling...',      cls: 'bg-yellow-50 text-yellow-700 border-yellow-200 animate-pulse' },
  active:     { label: 'In Conversation', cls: 'bg-purple-50 text-purple-700 border-purple-200 animate-pulse' },
  completed:  { label: 'Completed',       cls: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
  incomplete: { label: 'Incomplete',      cls: 'bg-orange-50 text-orange-700 border-orange-200' },
  missedcall: { label: 'Missed Call',     cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  // ── Legacy backward-compat ────────────────────────────────────────────────
  agent_ready: { label: 'Pending',     cls: 'bg-amber-50 text-amber-700 border-amber-100' },
  failed:      { label: 'Missed Call', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  timeout:     { label: 'Missed Call', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
};

function StatusBadge({ status }) {
  const cfg = STATUS_MAP[status] || { label: status, cls: 'bg-gray-50 text-gray-500 border-gray-100' };
  return (
    <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold border uppercase tracking-wider transition-all duration-300 ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}

export default function CustomerTable({ customers, onAction }) {
  return (
    /* Outer container: Uses flex-col and h-full to occupy available vertical space */
    <div className="flex flex-col h-full rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      
      {/* Scrollable Container: flex-1 allows it to grow, overflow-auto enables the scrollbar */}
      <div className="flex-1 overflow-auto scrollbar-thin scrollbar-thumb-gray-200">
        <table className="w-full text-left text-sm border-separate border-spacing-0">
          {/* Sticky header: backdrop-blur ensures readability when scrolling content underneath */}
          <thead className="sticky top-0 bg-white/95 backdrop-blur-sm z-10">
            <tr>
              <th className="p-4 border-b border-gray-100 font-bold text-gray-400 text-[11px] uppercase tracking-widest">
                Agreement Info
              </th>
              <th className="p-4 border-b border-gray-100 font-bold text-gray-400 text-[11px] uppercase tracking-widest">
                Customer Name
              </th>
              <th className="p-4 border-b border-gray-100 font-bold text-gray-400 text-[11px] uppercase tracking-widest">
                Contact
              </th>
              <th className="p-4 border-b border-gray-100 font-bold text-gray-400 text-[11px] uppercase tracking-widest">
                Call Lifecycle
              </th>
              <th className="p-4 border-b border-gray-100 font-bold text-gray-400 text-[11px] uppercase tracking-widest text-right">
                Actions
              </th>
            </tr>
          </thead>
          
          <tbody className="divide-y divide-gray-50">
            {customers.map((c) => (
              <tr 
                key={c.agreement_no} 
                className={`group transition-colors duration-150 ${
                  c.status === 'active' ? 'bg-purple-50/30' :
                  c.status === 'calling' ? 'bg-yellow-50/30' :
                  'hover:bg-gray-50/80'
                }`}
              >
                <td className="p-4">
                  <div className="font-mono text-xs font-semibold text-gray-600 bg-gray-100 px-2 py-1 rounded-md inline-block">
                    {c.agreement_no}
                  </div>
                </td>
                <td className="p-4 font-semibold text-gray-900">
                  {c.customer_name}
                </td>
                <td className="p-4 text-gray-500 tabular-nums font-medium">
                  {c.contact_number}
                </td>
                <td className="p-4">
                  <StatusBadge status={c.status} />
                </td>
                <td className="p-4 text-right">
                  <ActionButton 
                    status={c.status} 
                    onClick={() => onAction(c.agreement_no)} 
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* Empty State */}
        {customers.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400">
            <div className="w-12 h-12 bg-gray-50 rounded-full flex items-center justify-center mb-3">
              🔍
            </div>
            <p className="text-xs font-bold uppercase tracking-widest">No Agreements Found</p>
            <p className="text-[11px] mt-1">Upload a file to begin outreach</p>
          </div>
        )}
      </div>

      {/* Table Footer: shrink-0 ensures the footer is never compressed by the table */}
      <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 flex justify-between items-center shrink-0">
        <div className="flex gap-4">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            <span className="text-[11px] font-bold text-gray-500 uppercase">
              {customers.filter(c => c.status === 'completed').length} Done
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
            <span className="text-[11px] font-bold text-gray-500 uppercase tracking-tighter">
              {customers.filter(c => c.status === 'calling').length} Calling
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-purple-500" />
            <span className="text-[11px] font-bold text-gray-500 uppercase tracking-tighter">
              {customers.filter(c => c.status === 'active').length} Talking
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-orange-500" />
            <span className="text-[11px] font-bold text-gray-500 uppercase tracking-tighter">
              {customers.filter(c => c.status === 'incomplete').length} Incomplete
            </span>
          </div>
        </div>
        <div className="text-[10px] font-black text-gray-300 uppercase tracking-[0.2em]">
          ORM Linked System
        </div>
      </div>
    </div>
  );
}