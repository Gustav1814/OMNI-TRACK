import React, { createContext, useCallback, useContext, useState } from 'react';
import { CheckCircle2, AlertTriangle, AlertOctagon, Info, X } from 'lucide-react';

const ToastContext = createContext(null);

const ICONS = {
    success: CheckCircle2,
    error: AlertOctagon,
    warning: AlertTriangle,
    info: Info,
};

export function ToastProvider({ children }) {
    const [toasts, setToasts] = useState([]);

    const dismiss = useCallback((id) => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
    }, []);

    const toast = useCallback(({ title, message, type = 'info', duration = 4500 }) => {
        const id = `${Date.now()}-${Math.random()}`;
        setToasts((prev) => [...prev.slice(-4), { id, title, message, type }]);
        if (duration > 0) {
            setTimeout(() => dismiss(id), duration);
        }
        return id;
    }, [dismiss]);

    return (
        <ToastContext.Provider value={{ toast, dismiss }}>
            {children}
            <div className="ui-toast-stack" aria-live="polite">
                {toasts.map((t) => {
                    const Icon = ICONS[t.type] || Info;
                    return (
                        <div key={t.id} className={`ui-toast ui-toast--${t.type}`} role={t.type === 'error' ? 'alert' : 'status'}>
                            <Icon size={16} strokeWidth={1.75} className="ui-toast-icon" aria-hidden />
                            <div className="ui-toast-body">
                                {t.title ? <div className="ui-toast-title">{t.title}</div> : null}
                                {t.message ? <div className="ui-toast-message">{t.message}</div> : null}
                            </div>
                            <button type="button" className="ui-toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
                                <X size={14} strokeWidth={1.75} />
                            </button>
                        </div>
                    );
                })}
            </div>
        </ToastContext.Provider>
    );
}

export function useToast() {
    const ctx = useContext(ToastContext);
    if (!ctx) throw new Error('useToast must be used within ToastProvider');
    return ctx;
}
