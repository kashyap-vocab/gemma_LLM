import { useRef, useState, useEffect, useCallback } from 'react';
import Sidebar from './Sidebar';
import CustomerTable from './CustomerTable';

// How often (ms) to poll the backend for status updates on active calls
const POLL_INTERVAL_MS = 4000;
// Statuses that need no further polling
const TERMINAL_STATUSES = new Set(['completed', 'incomplete', 'missedcall', 'pending', 'failed', 'timeout']);

export default function DashboardLayout({
  customers,
  setCustomers,
  dateRange,
  setDateRange,
  filters,
  setFilters,
  onDownload,
  onReset,
  API_BASE,
}) {
  const [isRunning, setIsRunning] = useState(false);
  const eventSourceRef = useRef(null);
  const pollTimerRef   = useRef(null);

  // Always reflect latest customers in the poll closure without re-creating the interval
  const customersRef = useRef(customers);
  useEffect(() => { customersRef.current = customers; }, [customers]);

  // Clean up SSE + poll timer on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) eventSourceRef.current.close();
      if (pollTimerRef.current)   clearInterval(pollTimerRef.current);
    };
  }, []);

  /**
   * Poll /calls/status/{agreement_no} for every in-flight (non-terminal) row.
   * Stops automatically when all active rows reach a terminal state.
   */
  const pollActiveStatuses = useCallback(async () => {
    const inFlight = customersRef.current.filter((c) => !TERMINAL_STATUSES.has(c.status));
    if (inFlight.length === 0) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
      return;
    }
    await Promise.all(
      inFlight.map(async (c) => {
        try {
          const res  = await fetch(`${API_BASE}/calls/status/${c.agreement_no}`);
          const data = await res.json();
          if (data.call_status && data.call_status !== c.status) {
            setCustomers((prev) =>
              prev.map((row) =>
                row.agreement_no === c.agreement_no
                  ? { ...row, status: data.call_status }
                  : row
              )
            );
          }
        } catch (_) {
          // Network error — will retry next tick
        }
      })
    );
  }, [API_BASE, setCustomers]);

  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return;
    pollTimerRef.current = setInterval(pollActiveStatuses, POLL_INTERVAL_MS);
  }, [pollActiveStatuses]);

  /**
   * Helper: Update local React state to match Backend ORM state.
   * Matches by agreement_no (Relational PK)
   */
  const updateCustomerStatus = (agreementNo, status) => {
    setCustomers((prev) =>
      prev.map((c) => (c.agreement_no === agreementNo ? { ...c, status } : c))
    );
  };

  /**
   * Start Auto-Dialer (Relational logic)
   * Triggers the concurrent backend dialer and listens for status pushes.
   */
  const startAutoDialer = async () => {
    setIsRunning(true);
    try {
      // 1. Identify pending rows via agreement_no
      const pendingNos = customers
        .filter((c) => c.status === 'pending' || c.status === 'failed' || c.status === 'timeout')
        .map((c) => c.agreement_no);

      if (pendingNos.length === 0) {
        setIsRunning(false);
        return;
      }

      // 2. POST to ORM start endpoint
      await fetch(`${API_BASE}/auto-dialer/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agreement_nos: pendingNos }),
      });

      // 3. Connect to SSE Stream for live DB updates
      const es = new EventSource(`${API_BASE}/auto-dialer/events`);
      eventSourceRef.current = es;

      es.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.agreement_no) {
          updateCustomerStatus(data.agreement_no, data.type);
        }

        if (data.type === 'finished' || data.type === 'stopped') {
          es.close();
          setIsRunning(false);
          // Start polling to catch any final DB status updates after SSE ends
          startPolling();
        }
      };

      es.onerror = () => {
        console.error("SSE Connection lost.");
        es.close();
        setIsRunning(false);
        startPolling();
      };
    } catch (e) {
      console.error("Failed to start dialer:", e);
      setIsRunning(false);
    }
  };

  /**
   * Stop Dialer
   * Sends request to backend to stop picking up new slots.
   */
  const stopAutoDialer = async () => {
    try {
      await fetch(`${API_BASE}/auto-dialer/stop`, { method: 'POST' });
    } catch (e) {
      console.error("Stop request failed:", e);
    }
  };

  /**
   * Individual Manual Call
   * Step A: POST /calls/context  (creates CallMetadata, dispatches agent)
   * Step B: POST /calls/trigger  (marks active, fires SmartFlo)
   * Then starts the polling loop so the UI reflects real-time status changes.
   */
  const handleManualCall = async (agreementNo) => {
    const customer = customers.find((c) => c.agreement_no === agreementNo);
    if (!customer) return;

    updateCustomerStatus(agreementNo, 'calling');

    try {
      await fetch(`${API_BASE}/calls/context`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone_number: customer.contact_number,
          customer_name: customer.customer_name,
          agreement_no: agreementNo,
        }),
      });

      await fetch(`${API_BASE}/calls/trigger`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agreement_no: agreementNo,
          phone_number: customer.contact_number,
          customer_name: customer.customer_name,
        }),
      });

      // Begin polling so the row updates when the agent finishes the call
      startPolling();
    } catch (e) {
      console.error("Manual call trigger failed:", e);
      updateCustomerStatus(agreementNo, 'missedcall');
    }
  };

  return (
    <div className="h-screen flex flex-col bg-gray-50 font-sans antialiased">
      {/* ── Global Header ──────────────────────────── */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between shrink-0 shadow-sm z-10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-gray-900 rounded-lg flex items-center justify-center shadow-lg transition-transform hover:scale-105">
            <span className="text-white text-xs font-black tracking-tighter">LT</span>
          </div>
          <h1 className="font-bold text-gray-900 text-base tracking-tight">LTFS Call Manager</h1>
        </div>

        <button
          onClick={onReset}
          className="text-xs font-semibold text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-400 rounded-xl px-4 py-2 bg-white hover:bg-gray-50 transition-all active:scale-95 shadow-sm"
        >
          ＋ New Upload
        </button>
      </header>

      {/* ── Main Layout Body ────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Filter Sidebar */}
        <Sidebar
          dateRange={dateRange}
          setDateRange={setDateRange}
          filters={filters}
          setFilters={setFilters}
          onDownload={onDownload}
        />

        {/* Dynamic Content Area */}
        <main className="flex-1 flex flex-col overflow-hidden p-8 bg-[#fcfcfc]">
          <div className="flex items-end justify-between mb-8 shrink-0">
            <div>
              <h2 className="text-2xl font-extrabold text-gray-900 tracking-tight">Customer Outreach</h2>
              <p className="text-sm text-gray-500 font-medium mt-1">
                {isRunning ? (
                  <span className="flex items-center gap-2 text-blue-600">
                    <span className="w-2 h-2 rounded-full bg-blue-600 animate-ping" />
                    Auto-dialer is currently processing queue...
                  </span>
                ) : (
                  "Manage individual calls or start automated sequences."
                )}
              </p>
            </div>

            {/* Start / Stop Toggle */}
            {!isRunning ? (
              <button
                onClick={startAutoDialer}
                className="group flex items-center gap-2.5 px-6 py-3 rounded-2xl bg-gray-900 text-white font-bold text-sm hover:bg-gray-800 transition-all active:scale-95 shadow-xl shadow-gray-200"
              >
                <span className="text-xs group-hover:translate-x-0.5 transition-transform">▶</span>
                Start Auto-Dialer
              </button>
            ) : (
              <button
                onClick={stopAutoDialer}
                className="flex items-center gap-2.5 px-6 py-3 rounded-2xl bg-red-600 text-white font-bold text-sm hover:bg-red-700 transition-all active:scale-95 shadow-xl shadow-red-100"
              >
                <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
                Stop Dialer
              </button>
            )}
          </div>

          {/* Customer Table Component */}
          <div className="flex-1 overflow-hidden">
            <CustomerTable customers={customers} onAction={handleManualCall} />
          </div>
        </main>
      </div>
    </div>
  );
}