import {useState, useEffect} from 'react';
import InitialView from './components/InitialView';
import DashboardLayout from './components/DashboardLayout';

const API_BASE = '/api';

export default function App() {
    const [view, setView] = useState('initial');
    const [customers, setCustomers] = useState([]);
    const [dateRange, setDateRange] = useState({start: '', end: ''});
    const [filters, setFilters] = useState({disposition: 'All', tableName: 'customer_feedback'});

    // Load existing customers from DB on mount or when returning to dashboard
    const fetchCustomers = async () => {
        try {
            const response = await fetch(`${API_BASE}/customers`);
            const data = await response.json();
            // Map 'call_status' from backend to 'status' for UI
            setCustomers(data.map(c => ({...c, status: c.call_status || 'pending'})));
            return data;
        } catch (error) {
            console.error("Failed to fetch customers", error);
            return [];
        }
    };

    useEffect(() => {
        fetchCustomers().then(data => {
            if (data && data.length > 0) {
                setView('dashboard');
            }
        });
    }, []);

    const handleUpload = async (file) => {
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${API_BASE}/customers/upload`, {
                method: 'POST',
                body: formData,
            });
            const result = await response.json();
            if (result.success) {
                await fetchCustomers();
                setView('dashboard');
            } else {
                alert("Error: " + result.detail);
            }
        } catch (error) {
            alert("Upload failed. Check backend connection.");
        }
    };

    const handleDownload = () => {
        const params = new URLSearchParams({
            start: dateRange.start,
            end: dateRange.end,
            disposition: filters.disposition,
            table: filters.tableName,
        });
        window.open(`${API_BASE}/customers/download?${params.toString()}`, '_blank');
    };

    const handleReset = () => {
        setView('initial');
        setCustomers([]);
    };

    return (
        <div className="min-h-screen bg-gray-50">
            {view === 'initial' ? (
                <InitialView
                    dateRange={dateRange}
                    setDateRange={setDateRange}
                    filters={filters}
                    setFilters={setFilters}
                    onUpload={handleUpload}
                    onDownload={handleDownload}
                />
            ) : (
                <DashboardLayout
                    customers={customers}
                    setCustomers={setCustomers}
                    dateRange={dateRange}
                    setDateRange={setDateRange}
                    filters={filters}
                    setFilters={setFilters}
                    onDownload={handleDownload}
                    onReset={handleReset}
                    API_BASE={API_BASE}
                />
            )}
        </div>
    );
}