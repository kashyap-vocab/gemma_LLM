import React, { useState } from 'react';
import axios from 'axios';
import './CustomerList.css';

function CustomerList({ customers, loading, onRefresh }) {
  const [calling, setCalling] = useState({});
  const [searchTerm, setSearchTerm] = useState('');

  const handleCall = async (customer) => {
    const customerId = customer.id;
    setCalling({ ...calling, [customerId]: true });

    try {
      const response = await axios.post('/api/calls/trigger', {
        customer_id: customerId,
        phone_number: customer.contact_number,
        customer_name: customer.customer_name,
      });

      alert(`✅ ${response.data.message}\n\nCall will be initiated to ${customer.customer_name} at ${customer.contact_number}`);
      
      // Refresh the list
      if (onRefresh) {
        onRefresh();
      }
    } catch (error) {
      alert(`❌ Failed to trigger call: ${error.response?.data?.detail || error.message}`);
    } finally {
      setCalling({ ...calling, [customerId]: false });
    }
  };

  const filteredCustomers = customers.filter(
    (customer) =>
      customer.customer_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      customer.contact_number?.includes(searchTerm)
  );

  if (loading) {
    return (
      <div className="card">
        <h2>📋 Customer List</h2>
        <div className="loading">Loading customers...</div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="customer-list-header">
        <h2>📋 Customer List</h2>
        <div className="search-box">
          <input
            type="text"
            placeholder="Search by name or phone number..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
          <span className="search-icon">🔍</span>
        </div>
        <div className="customer-count">
          Showing {filteredCustomers.length} of {customers.length} customers
        </div>
      </div>

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
                <tr key={customer.id}>
                  <td>{customer.id}</td>
                  <td className="customer-name">{customer.customer_name}</td>
                  <td className="contact-number">{customer.contact_number}</td>
                  <td>{customer.agreement_no || '-'}</td>
                  <td>{customer.branch || '-'}</td>
                  <td>
                    {customer.emi
                      ? `₹${parseFloat(customer.emi).toLocaleString('en-IN')}`
                      : '-'}
                  </td>
                  <td>{customer.state || '-'}</td>
                  <td>
                    <button
                      className="btn btn-success"
                      onClick={() => handleCall(customer)}
                      disabled={calling[customer.id]}
                    >
                      {calling[customer.id] ? (
                        <>
                          <span className="spinner-small"></span>
                          Calling...
                        </>
                      ) : (
                        <>
                          📞 Call
                        </>
                      )}
                    </button>
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
