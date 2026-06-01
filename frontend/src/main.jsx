import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import './styles/enterprise.css'
import './styles/components.css'
import './styles/pearl.css'
import './styles/sidebar.css'
import './styles/cards.css'
import './styles/magic-card.css'
import './styles/gradients.css'
import { ThemeProvider } from './contexts/ThemeContext'
import { ToastProvider } from './contexts/ToastContext'

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <ThemeProvider>
            <ToastProvider>
                <App />
            </ToastProvider>
        </ThemeProvider>
    </React.StrictMode>,
)
