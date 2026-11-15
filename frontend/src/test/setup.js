import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => cleanup());

// Browser APIs jsdom does not provide. They are inert: no data is produced.
if (!window.matchMedia) {
    window.matchMedia = (query) => ({
        matches: false, media: query, onchange: null,
        addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
    });
}
class NoopObserver { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
globalThis.ResizeObserver = globalThis.ResizeObserver || NoopObserver;
globalThis.IntersectionObserver = globalThis.IntersectionObserver || NoopObserver;
window.scrollTo = window.scrollTo || (() => {});
HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
URL.createObjectURL = URL.createObjectURL || (() => 'blob:test');

// Live sockets never connect in tests.
class FakeSocket {
    constructor(url) { this.url = url; this.readyState = 3; }
    send() {} close() {} addEventListener() {} removeEventListener() {}
}
FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1; FakeSocket.CLOSING = 2; FakeSocket.CLOSED = 3;
globalThis.WebSocket = FakeSocket;
