import React from 'react';
import { useNavigate } from 'react-router-dom';

function Dashboard({ token }) {
  const navigate = useNavigate();

  const handleLogout = () => {
    localStorage.removeItem('authToken');
    navigate('/login');
  };

  return (
    <div style={{ maxWidth: '800px', margin: 'auto', padding: '20px' }}>
      <h2>Welcome to the Dashboard</h2>
      <p>You are successfully logged in.</p>

      <div style={{ marginTop: '20px' }}>
        <h3>Quick Links</h3>
        <ul>
          <li><button onClick={() => navigate('/locations')}>Manage Locations</button></li>
          <li><button onClick={() => navigate('/shifts')}>Manage Shifts</button></li>
          <li><button onClick={() => navigate('/assignments')}>Assignments</button></li>
          <li><button onClick={() => navigate('/checkin')}>Check-In</button></li>
          <li><button onClick={() => navigate('/tourlogs')}>Tour Logs</button></li>
          <li><button onClick={() => navigate('/incidents')}>Incident Reports</button></li>
          <li><button onClick={() => navigate('/export')}>Export Logs</button></li>
        </ul>
      </div>

      <button style={{ marginTop: '30px' }} onClick={handleLogout}>Logout</button>
    </div>
  );
}

export default Dashboard;

