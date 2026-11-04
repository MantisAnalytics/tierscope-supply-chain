import { createContext, useCallback, useContext, useRef, useState, type FormEvent, type ReactNode } from "react";
import { storeAuthor, storedAuthor } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";

interface AuthorContext {
  author: string | null;
  /** Run a change; asks for the name first when none is stored. */
  requireAuthor: (action: () => void) => void;
  /** Open the name dialog to change the stored name. */
  editAuthor: () => void;
}

const Ctx = createContext<AuthorContext | null>(null);

export function AuthorProvider({ children }: { children: ReactNode }) {
  const [author, setAuthor] = useState<string | null>(storedAuthor);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const pending = useRef<(() => void) | null>(null);

  const requireAuthor = useCallback((action: () => void) => {
    const current = storedAuthor();
    if (current) {
      action();
      return;
    }
    pending.current = action;
    setDraft("");
    setOpen(true);
  }, []);

  const editAuthor = useCallback(() => {
    pending.current = null;
    setDraft(storedAuthor() ?? "");
    setOpen(true);
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const name = draft.trim();
    if (!name) return;
    storeAuthor(name);
    setAuthor(name);
    setOpen(false);
    const action = pending.current;
    pending.current = null;
    action?.();
  };

  return (
    <Ctx.Provider value={{ author, requireAuthor, editAuthor }}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) pending.current = null;
        }}
      >
        <DialogContent className="max-w-sm">
          <form onSubmit={submit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Your name</DialogTitle>
              <DialogDescription>Changes are recorded with who made them. Stored in this browser only.</DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="author-name">Name</Label>
              <Input id="author-name" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="e.g. Ana Kovalenko" />
            </div>
            <DialogFooter>
              <Button type="submit" variant="accent" disabled={!draft.trim()}>
                {pending.current ? "Save and continue" : "Save"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Ctx.Provider>
  );
}

export function useAuthor() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuthor outside AuthorProvider");
  return ctx;
}
