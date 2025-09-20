import React, { useState } from 'react';
import axios from 'axios';

function App() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [response, setResponse] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const res = await axios.post('http://147.93.27.224:8001/auth/users/', {
        email,
        password
      });
      setResponse('User created successfully!');
    } catch (error) {
      setResponse(error.response?.data?.detail || 'Error creating user');
    }
  };

  return (
    <div style={{ padding: '2rem' }}>
      <h2>Create User</h2>
      <form onSubmit={handleSubmit}>
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        /><br /><br />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        /><br /><br />
        <button type="submit">Create User</button>
      </form>
      <p>{response}</p>
    </div>
  );
}

export default App;

