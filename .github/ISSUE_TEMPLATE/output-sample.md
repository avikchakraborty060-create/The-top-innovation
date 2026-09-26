---
name: Output sample
about: A command whose output skimread digests badly (too much kept, or something important hidden)
labels: fixture
---

**Command**

```
<the exact command line>
```

**What went wrong**

- [ ] Too much noise kept
- [ ] Something important was hidden (say what)
- [ ] Wrong format detected (`skimread list` shows the format)

**Sample**

Attach the raw output captured with `NO_COLOR=1 <command> > sample.txt 2>&1`, with private paths and tokens removed.
