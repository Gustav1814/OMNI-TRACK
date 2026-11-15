/**
 * Smoke tests: every page renders against a backend that has no data.
 * Every API call resolves with `null` (exactly what useLivePoll holds before
 * the first response), so a page that crashes or shows numbers here is
 * inventing them.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ThemeProvider } from '../contexts/ThemeContext';
import { ToastProvider } from '../contexts/ToastContext';

const calls = [];

vi.mock('../services/api', async (importOriginal) => {
    const real = await importOriginal();
    const empty = (name) => (...args) => { calls.push([name, args]); return Promise.resolve({ data: null }); };
    const mocked = {};
    for (const [key, value] of Object.entries(real)) {
        if (value && typeof value === 'object' && key.endsWith('API')) {
            mocked[key] = Object.fromEntries(Object.keys(value).map((m) => [m, empty(`${key}.${m}`)]));
        } else {
            mocked[key] = value;
        }
    }
    const http = { get: empty('api.get'), post: empty('api.post'), put: empty('api.put'), patch: empty('api.patch'), delete: empty('api.delete') };
    return { ...mocked, default: { ...real.default, ...http } };
});

vi.mock('axios', async (importOriginal) => {
    const real = await importOriginal();
    const fail = () => Promise.reject(new Error('network disabled in tests'));
    const instance = { get: fail, post: fail, put: fail, patch: fail, delete: fail,
        interceptors: { request: { use() {} }, response: { use() {} } }, defaults: { headers: { common: {} } } };
    return { ...real, default: { ...real.default, create: () => instance, get: fail, post: fail } };
});

// HubPage is a layout that needs a hub definition; it has its own test below.
const pages = Object.fromEntries(
    Object.entries(import.meta.glob('../pages/*.jsx')).filter(([p]) => !p.endsWith('/HubPage.jsx')),
);

async function renderPage(loader) {
    const { default: Page } = await loader();
    let utils;
    await act(async () => {
        utils = render(
            <ThemeProvider>
                <ToastProvider>
                    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
                        <Suspense fallback={<div>loading</div>}>
                            <Page onLogin={() => {}} onRegister={() => {}} />
                        </Suspense>
                    </MemoryRouter>
                </ToastProvider>
            </ThemeProvider>,
        );
    });
    // let the first poll resolve
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    return utils;
}

beforeEach(() => {
    calls.length = 0;
    localStorage.setItem('omnitrack_token', 'test-token');
    localStorage.setItem('omnitrack_user', JSON.stringify({ username: 'test', role: 'admin' }));
});

describe('pages render with an empty backend', () => {
    for (const [path, loader] of Object.entries(pages)) {
        const name = path.split('/').pop().replace('.jsx', '');
        it(name, async () => {
            const errors = [];
            const spy = vi.spyOn(console, 'error').mockImplementation((...a) => { errors.push(a.map(String).join(' ')); });
            const { container } = await renderPage(loader);
            spy.mockRestore();
            expect(container.textContent.length).toBeGreaterThan(0);
            const crashes = errors.filter((e) => /The above error occurred|Uncaught|is not a function|Cannot read prop/i.test(e));
            expect(crashes).toEqual([]);
        });
    }
});

describe('EvaluationPage', () => {
    it('shows an empty state and no numbers when nothing was measured', async () => {
        await renderPage(pages['../pages/EvaluationPage.jsx']);
        expect(screen.getByText(/No evaluation results yet/i)).toBeInTheDocument();
        expect(calls.some(([n]) => n === 'evaluationAPI.summary')).toBe(true);
    });
});

describe('HubPage', () => {
    const Alerts = ({ embedded }) => <p>alerts view {embedded ? 'embedded' : 'standalone'}</p>;
    const Captures = () => <p>captures view</p>;
    const renderHub = async (url) => {
        const { default: HubPage } = await import('../pages/HubPage');
        const views = { alerts: Alerts, captures: Captures };
        await act(async () => {
            render(
                <MemoryRouter initialEntries={[url]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
                    <Routes>
                        <Route path="/incidents" element={<HubPage path="/incidents" views={views} />} />
                        <Route path="/incidents/:section" element={<HubPage path="/incidents" views={views} />} />
                    </Routes>
                </MemoryRouter>,
            );
        });
    };

    it('serves the first section at the hub path, embedded, with tabs for the views it has', async () => {
        await renderHub('/incidents');
        expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Incidents');
        expect(screen.getByText('alerts view embedded')).toBeTruthy();
        const tabs = screen.getAllByRole('link');
        expect(tabs.map((t) => t.getAttribute('href'))).toEqual(['/incidents/alerts', '/incidents/captures']);
        expect(tabs[0].getAttribute('aria-current')).toBe('page');
    });

    it('opens the section named in the URL', async () => {
        await renderHub('/incidents/captures');
        expect(screen.getByText('captures view')).toBeTruthy();
    });

    it('redirects an unknown section to the hub', async () => {
        await renderHub('/incidents/nope');
        expect(screen.getByText('alerts view embedded')).toBeTruthy();
    });
});
