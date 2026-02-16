import React, { useState } from 'react';
import { useDropzone } from 'react-dropzone';
import axios from 'axios';
import './CustomerUpload.css';

function CustomerUpload({ onUploadSuccess }) {
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);

  const onDrop = async (acceptedFiles) => {
    if (acceptedFiles.length === 0) return;

    const file = acceptedFiles[0];
    setUploading(true);
    setMessage(null);
    setError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await axios.post('/api/customers/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      setMessage(
        `✅ ${response.data.message} (${response.data.inserted} inserted, ${response.data.updated} updated)`
      );
      if (onUploadSuccess) {
        onUploadSuccess();
      }
    } catch (err) {
      setError(
        err.response?.data?.detail || err.message || 'Failed to upload file'
      );
    } finally {
      setUploading(false);
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
      'application/vnd.ms-excel': ['.xls'],
    },
    multiple: false,
  });

  return (
    <div className="card">
      <h2>📤 Upload Customer Data</h2>
      
      {message && <div className="success">{message}</div>}
      {error && <div className="error">{error}</div>}

      <div
        {...getRootProps()}
        className={`upload-zone ${isDragActive ? 'active' : ''} ${uploading ? 'uploading' : ''}`}
      >
        <input {...getInputProps()} disabled={uploading} />
        <div className="upload-content">
          {uploading ? (
            <>
              <div className="spinner"></div>
              <p>Uploading and processing Excel file...</p>
            </>
          ) : isDragActive ? (
            <>
              <span className="upload-icon">📁</span>
              <p>Drop the Excel file here...</p>
            </>
          ) : (
            <>
              <span className="upload-icon">📄</span>
              <p>
                <strong>Drag & drop</strong> an Excel file here, or{' '}
                <strong>click to select</strong>
              </p>
              <p className="upload-hint">
                Supported formats: .xlsx, .xls
              </p>
            </>
          )}
        </div>
      </div>

      <div className="upload-info">
        <h3>Required Excel Columns:</h3>
        <ul>
          <li><strong>Customer Name</strong> (required)</li>
          <li><strong>Contact Number</strong> (required)</li>
          <li>Agreement No, Branch, Zone, Product, EMI, State, Area, and more...</li>
        </ul>
      </div>
    </div>
  );
}

export default CustomerUpload;
