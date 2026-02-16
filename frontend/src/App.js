import React, { useState, useEffect } from 'react';
import './App.css';
import CustomerUpload from './components/CustomerUpload';
import CustomerList from './components/CustomerList';

function App() {
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const fetchCustomers = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/customers?limit=1000');
      if (response.ok) {
        const data = await response.json();
        setCustomers(data);
      }
    } catch (error) {
      console.error('Error fetching customers:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCustomers();
  }, [refreshKey]);

  const handleUploadSuccess = () => {
    setRefreshKey(prev => prev + 1);
  };

  return (
    <div className="App">
      <header className="App-header">
        <h1>L&T Finance Customer Survey Agent</h1>
        <p>Upload Excel sheets and manage customer calls</p>
      </header>
      
      <main className="App-main">
        <CustomerUpload onUploadSuccess={handleUploadSuccess} />
        <CustomerList 
          customers={customers} 
          loading={loading}
          onRefresh={fetchCustomers}
        />
      </main>
    </div>
  );
}

export default App;
