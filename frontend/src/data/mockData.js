export const DISPOSITIONS = [
  'All',
  'Paid',
  'PTP',
  'Disputed',
  'No Answer',
  'Wrong Number',
  'Busy',
  'Callback Requested',
  'Disconnected',
]

export const TABLE_NAMES = [
  'customer_feedback',
  'conversation',
  'call_metadata',
  'customer',
]

// Mock customer rows — mimics data from the uploaded Excel / DB
export const MOCK_CUSTOMERS = [
  { id: 1,  agreement_no: 'LTF2024001', customer_name: 'Rajesh Kumar',    contact_number: '9876543210', status: 'pending'  },
  { id: 2,  agreement_no: 'LTF2024002', customer_name: 'Priya Sharma',    contact_number: '8765432109', status: 'done'     },
  { id: 3,  agreement_no: 'LTF2024003', customer_name: 'Amit Verma',      contact_number: '7654321098', status: 'pending'  },
  { id: 4,  agreement_no: 'LTF2024004', customer_name: 'Sunita Patel',    contact_number: '9543210987', status: 'disabled' },
  { id: 5,  agreement_no: 'LTF2024005', customer_name: 'Vikram Singh',    contact_number: '8432109876', status: 'pending'  },
  { id: 6,  agreement_no: 'LTF2024006', customer_name: 'Deepa Nair',      contact_number: '7321098765', status: 'done'     },
  { id: 7,  agreement_no: 'LTF2024007', customer_name: 'Suresh Reddy',    contact_number: '9210987654', status: 'pending'  },
  { id: 8,  agreement_no: 'LTF2024008', customer_name: 'Kavya Menon',     contact_number: '8109876543', status: 'pending'  },
  { id: 9,  agreement_no: 'LTF2024009', customer_name: 'Ramesh Gupta',    contact_number: '9098765432', status: 'disabled' },
  { id: 10, agreement_no: 'LTF2024010', customer_name: 'Anita Joshi',     contact_number: '7987654321', status: 'done'     },
  { id: 11, agreement_no: 'LTF2024011', customer_name: 'Mahesh Yadav',    contact_number: '9876012345', status: 'pending'  },
  { id: 12, agreement_no: 'LTF2024012', customer_name: 'Rekha Iyer',      contact_number: '8765901234', status: 'pending'  },
  { id: 13, agreement_no: 'LTF2024013', customer_name: 'Kiran Bose',      contact_number: '7654890123', status: 'done'     },
  { id: 14, agreement_no: 'LTF2024014', customer_name: 'Pooja Desai',     contact_number: '9543789012', status: 'pending'  },
  { id: 15, agreement_no: 'LTF2024015', customer_name: 'Harish Nambiar',  contact_number: '8432678901', status: 'disabled' },
]
