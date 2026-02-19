import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import './CustomerList.css';

function CustomerList({ customers, loading, onRefresh }) {
  const [calling, setCalling] = useState({});
  const [done, setDone] = useState({});
  const [searchTerm, setSearchTerm] = useState('');

  // Auto-dialer state
  const [autoCallActive, setAutoCallActive] = useState(false);
  const [autoCallProgress, setAutoCallProgress] = useState({
    currentName: '',
    currentIndex: -1,
    total: 0,
    completed: 0,
    failed: 0,
  });
  const [autoCallLog, setAutoCallLog] = useState([]);
  const [activeCustomerId, setActiveCustomerId] = useState(null);

  // Ref to close SSE connection when stopping
  const eventSourceRef = useRef(null);

  // On mount, check if auto-dialer is already running (page refresh case)
  useEffect(() => {
    axios.get('/api/auto-dialer/status').then((res) => {
      if (res.data.active) {
        setAutoCallActive(true);
        setAutoCallProgress({
          currentName: res.data.current_customer || '',
          currentIndex: res.data.current_index,
          total: res.data.total,
          completed: res.data.completed,
          failed: res.data.failed,
        });
        connectSSE();
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connectSSE = () => {
    // Close any existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const es = new EventSource('/api/auto-dialer/events');
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleSSEEvent(data);
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    es.onerror = (err) => {
      console.error('SSE connection error:', err);
      es.close();
    };
  };

  const handleSSEEvent = (data) => {
    switch (data.type) {
      case 'status':
        // Initial status snapshot on connect
        setAutoCallProgress({
          currentName: data.current_customer || '',
          currentIndex: data.current_index,
          total: data.total,
          completed: data.completed,
          failed: data.failed,
        });
        break;

      case 'calling':
        setActiveCustomerId(data.customer_id);
        setCalling((prev) => ({ ...prev, [data.customer_id]: true }));
        setAutoCallProgress({
          currentName: data.customer_name,
          currentIndex: data.index,
          total: data.total,
          completed: data.completed || 0,
          failed: data.failed || 0,
        });
        setAutoCallLog((prev) => [
          ...prev,
          { id: data.customer_id, name: data.customer_name, status: 'calling' },
        ]);
        break;

      case 'completed':
        setCalling((prev) => ({ ...prev, [data.customer_id || activeCustomerId]: false }));
        setDone((prev) => ({ ...prev, [data.customer_id || activeCustomerId]: true }));
        setAutoCallProgress((prev) => ({
          ...prev,
          completed: data.completed,
          failed: data.failed,
        }));
        setAutoCallLog((prev) =>
          prev.map((item) =>
            item.name === data.customer_name ? { ...item, status: 'completed' } : item
          )
        );
        break;

      case 'failed':
        setCalling((prev) => ({ ...prev, [activeCustomerId]: false }));
        setAutoCallProgress((prev) => ({
          ...prev,
          completed: data.completed,
          failed: data.failed,
        }));
        setAutoCallLog((prev) =>
          prev.map((item) =>
            item.name === data.customer_name ? { ...item, status: 'failed', reason: data.reason } : item
          )
        );
        break;

      case 'timeout':
        setCalling((prev) => ({ ...prev, [activeCustomerId]: false }));
        setAutoCallProgress((prev) => ({
          ...prev,
          completed: data.completed,
          failed: data.failed,
        }));
        setAutoCallLog((prev) =>
          prev.map((item) =>
            item.name === data.customer_name ? { ...item, status: 'timeout' } : item
          )
        );
        break;

      case 'stopped':
        setCalling({});
        setAutoCallActive(false);
        setActiveCustomerId(null);
        setAutoCallLog((prev) =>
          prev.map((item) =>
            item.status === 'calling' ? { ...item, status: 'stopped' } : item
          )
        );
        if (eventSourceRef.current) eventSourceRef.current.close();
        break;

      case 'finished':
        setCalling({});
        setAutoCallActive(false);
        setActiveCustomerId(null);
        if (eventSourceRef.current) eventSourceRef.current.close();
        if (onRefresh) onRefresh();
        break;

      case 'heartbeat':
        // Keep-alive ping, ignore
        break;

      default:
        break;
    }
  };

  const handleStartAutoCall = async () => {
    try {
      const customerIds = filteredCustomers.map(c => c.id);
      const res = await axios.post('/api/auto-dialer/start', { customer_ids: customerIds });
      if (!res.data.success) {
        alert(res.data.message);
        return;
      }
      setAutoCallActive(true);
      setAutoCallLog([]);
      setDone({});
      setCalling({});
      setAutoCallProgress({
        currentName: '',
        currentIndex: -1,
        total: res.data.total,
        completed: 0,
        failed: 0,
      });
      connectSSE();
    } catch (err) {
      alert(`Failed to start auto-dialer: ${err.response?.data?.detail || err.message}`);
    }
  };

  const handleStopAutoCall = async () => {
    try {
      await axios.post('/api/auto-dialer/stop');
    } catch (err) {
      console.error('Stop error:', err);
    }
  };

  // Manual call (individual Call button)
  const handleCall = async (customer) => {
    const customerId = customer.id;
    setCalling({ ...calling, [customerId]: true });

    try {
      console.log(`[Step 1] Dispatching agent for ${customer.customer_name}...`);
      const contextResponse = await axios.post('/api/calls/context', {
        phone_number: customer.contact_number,
        customer_name: customer.customer_name,
        customer_id: customerId,
      });
      console.log(`[Step 1] Agent dispatched:`, contextResponse.data);

      console.log(`[Step 2] Waiting 5s for agent to initialize...`);
      await new Promise(resolve => setTimeout(resolve, 5000));

      console.log(`[Step 3] Triggering SmartFlo call to ${customer.contact_number}...`);
      const callResponse = await axios.post('/api/calls/trigger', {
        customer_id: customerId,
        phone_number: customer.contact_number,
        customer_name: customer.customer_name,
      });
      console.log(`[Step 3] Call triggered:`, callResponse.data);

      setDone({ ...done, [customerId]: true });

      if (onRefresh) {
        onRefresh();
      }
    } catch (error) {
      console.error('Call flow error:', error);
      alert(`Failed: ${error.response?.data?.detail || error.message}`);
    } finally {
      setCalling({ ...calling, [customerId]: false });
    }
  };

  const filteredCustomers = customers.filter(
    (customer) =>
      customer.customer_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      customer.contact_number?.includes(searchTerm)
  );

  const progressPct =
    autoCallProgress.total > 0
      ? Math.round(
          ((autoCallProgress.completed + autoCallProgress.failed) / autoCallProgress.total) * 100
        )
      : 0;

  if (loading) {
    return (
      <div className="card">
        <h2>Customer List</h2>
        <div className="loading">Loading customers...</div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="customer-list-header">
        <h2>Customer List</h2>
        <div className="search-box">
          <input
            type="text"
            placeholder="Search by name or phone..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
        </div>
        <div className="customer-count">
          {filteredCustomers.length} of {customers.length}
        </div>
      </div>

      {/* ── Auto-Dialer Controls ── */}
      <div className="auto-call-controls">
        <div className="auto-call-buttons">
          {!autoCallActive ? (
            <button
              className="btn btn-auto-start"
              onClick={handleStartAutoCall}
              disabled={customers.length === 0}
            >
              ▶ Start Auto Call&nbsp;
              <span className="auto-call-count">({customers.length})</span>
            </button>
          ) : (
            <button className="btn btn-auto-stop" onClick={handleStopAutoCall}>
              ■ Stop Auto Call
            </button>
          )}
        </div>

        {(autoCallActive || autoCallProgress.total > 0) && (
          <div className="auto-call-progress">
            <div className="progress-bar-container">
              <div
                className="progress-bar-fill"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <div className="progress-text">
              {autoCallActive ? (
                <>
                  Calling: <strong>{autoCallProgress.currentName || '...'}</strong>
                  &nbsp;[{autoCallProgress.currentIndex + 1}/{autoCallProgress.total}]
                  &nbsp;·&nbsp;
                </>
              ) : (
                <strong>Finished&nbsp;·&nbsp;</strong>
              )}
              <span className="prog-completed">✓ {autoCallProgress.completed}</span>
              &nbsp;·&nbsp;
              <span className="prog-failed">✗ {autoCallProgress.failed}</span>
              &nbsp;·&nbsp;{progressPct}%
            </div>
          </div>
        )}
      </div>

      {/* ── Auto-Call Log ── */}
      {autoCallLog.length > 0 && (
        <div className="auto-call-log">
          <div className="auto-call-log-title">Call Log</div>
          {autoCallLog.map((entry, idx) => (
            <div key={idx} className="log-entry">
              <span className="log-index">{idx + 1}.</span>
              <span className="log-name">{entry.name}</span>
              <span className={`log-status status-${entry.status}`}>
                {entry.status === 'calling'   ? '⏳ Calling…'   :
                 entry.status === 'completed' ? '✓ Completed'   :
                 entry.status === 'failed'    ? '✗ Failed'      :
                 entry.status === 'timeout'   ? '⏱ Timed Out'   :
                 entry.status === 'stopped'   ? '◼ Stopped'     : entry.status}
              </span>
              {entry.reason && (
                <span className="log-reason">{entry.reason}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {filteredCustomers.length === 0 ? (
        <div className="empty-state">
          <p>No customers found. Upload an Excel file to get started.</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="customer-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Customer Name</th>
                <th>Contact Number</th>
                <th>Agreement No</th>
                <th>Branch</th>
                <th>EMI</th>
                <th>State</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredCustomers.map((customer) => (
                <tr
                  key={customer.id}
                  className={activeCustomerId === customer.id ? 'row-active-call' : ''}
                >
                  <td>{customer.id}</td>
                  <td className="customer-name">{customer.customer_name}</td>
                  <td className="contact-number">{customer.contact_number}</td>
                  <td>{customer.agreement_no || '-'}</td>
                  <td>{customer.branch || '-'}</td>
                  <td>
                    {customer.emi
                      ? `${parseFloat(customer.emi).toLocaleString('en-IN')}`
                      : '-'}
                  </td>
                  <td>{customer.state || '-'}</td>
                  <td>
                    {done[customer.id] ? (
                      <button className="btn btn-done" disabled>
                        Done
                      </button>
                    ) : (
                      <button
                        className="btn btn-success"
                        onClick={() => handleCall(customer)}
                        disabled={calling[customer.id] || autoCallActive}
                        title={autoCallActive ? 'Auto-call is running' : ''}
                      >
                        {calling[customer.id] ? (
                          <>
                            <span className="spinner-small"></span>
                            Calling...
                          </>
                        ) : (
                          'Call'
                        )}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default CustomerList;
