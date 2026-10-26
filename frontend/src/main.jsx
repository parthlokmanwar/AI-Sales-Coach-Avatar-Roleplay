import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
// "Calm Field" redesign (direction A × D) — single override layer on top of the
// original glassmorphism theme. Remove this line to revert the whole redesign.
import './theme-calm.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
