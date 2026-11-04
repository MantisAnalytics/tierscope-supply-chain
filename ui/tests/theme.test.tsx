import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { initialTheme, ThemeProvider, useTheme } from "@/app/theme";

function Toggle() {
  const { theme, toggle } = useTheme();
  return <button onClick={toggle}>theme {theme}</button>;
}

describe("Switch theme", () => {
  it("remembers the dark theme across a reload", async () => {
    const first = render(<ThemeProvider><Toggle /></ThemeProvider>);
    expect(screen.getByRole("button")).toHaveTextContent("theme light");
    await userEvent.click(screen.getByRole("button"));
    expect(document.documentElement).toHaveClass("dark");
    first.unmount();
    document.documentElement.classList.remove("dark"); // a reload starts from a fresh document
    render(<ThemeProvider><Toggle /></ThemeProvider>);
    expect(screen.getByRole("button")).toHaveTextContent("theme dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("follows the system preference on a first visit", () => {
    const spy = vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    expect(initialTheme()).toBe("dark");
    spy.mockReturnValue({ matches: false } as MediaQueryList);
    expect(initialTheme()).toBe("light");
    localStorage.setItem("tierscope.theme", "dark");
    expect(initialTheme()).toBe("dark");
    spy.mockRestore();
  });

  it("switches back to light", async () => {
    localStorage.setItem("tierscope.theme", "dark");
    render(<ThemeProvider><Toggle /></ThemeProvider>);
    await act(() => userEvent.click(screen.getByRole("button")));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("tierscope.theme")).toBe("light");
  });
});

// WCAG relative luminance and contrast of two #rrggbb colours.
const luminance = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe("Risk colour is reserved", () => {
  const css = readFileSync("src/index.css", "utf8");
  const block = (selector: string) => css.slice(css.indexOf(`${selector} {`), css.indexOf("}", css.indexOf(`${selector} {`)));
  const token = (selector: string, name: string) => block(selector).match(new RegExp(`--${name}: (#[0-9a-f]{6})`, "i"))![1];

  it.each([":root", ".dark"])("keeps rose risk text at WCAG AA on bg and surface in %s", (theme) => {
    const risk = token(theme, "risk");
    for (const bg of ["bg", "surface", "surface-2"]) expect(contrast(risk, token(theme, bg))).toBeGreaterThanOrEqual(4.5);
  });
});
