import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";
import { App } from "@/app/App";
import { makeQueryClient, Providers } from "@/app/providers";

export interface Call {
  method: string;
  url: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => unknown | Response | Promise<unknown | Response>;

/** Mock `fetch` with handlers keyed "METHOD /api/path" (query ignored); unknown calls fail the test. */
export function mockApi(handlers: Record<string, Handler | unknown>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.split("?")[0];
    const headers = Object.fromEntries(Object.entries((init.headers ?? {}) as Record<string, string>));
    let body: unknown = init.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        // text body
      }
    }
    const call = { method, url, path, headers, body };
    calls.push(call);
    const handler = handlers[`${method} ${path}`];
    if (handler === undefined) throw new Error(`unexpected request ${method} ${url}`);
    const result = typeof handler === "function" ? await (handler as Handler)(call) : handler;
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let currentPath = "/";
let currentSearch = "";
function Location() {
  const l = useLocation();
  currentPath = l.pathname;
  currentSearch = l.search;
  return null;
}
export const location = () => currentPath;
/** The current search string, decoded for readable assertions. */
export const search = () => decodeURIComponent(currentSearch);

/** The whole app at a path, with fresh query cache. */
export function renderApp(path = "/") {
  const client = makeQueryClient();
  client.setDefaultOptions({ ...client.getDefaultOptions(), queries: { ...client.getDefaultOptions().queries, retry: false, staleTime: 0 } });
  return {
    client,
    ...render(
      <Providers client={client}>
        <MemoryRouter initialEntries={[path]}>
          <App />
          <Location />
        </MemoryRouter>
      </Providers>,
    ),
  };
}

/** One component inside the providers and a router. */
export function renderWith(ui: ReactElement, path = "/", pattern = "*") {
  const client = makeQueryClient();
  client.setDefaultOptions({ ...client.getDefaultOptions(), queries: { ...client.getDefaultOptions().queries, retry: false, staleTime: 0 } });
  return render(
    <Providers client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={pattern} element={ui} />
        </Routes>
        <Location />
      </MemoryRouter>
    </Providers>,
  );
}
