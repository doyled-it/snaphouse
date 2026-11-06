# Contributing

`snaphouse` is one of several repos in this system. A feature usually spans
several of them, and the seams between repos (the API contract, config/env,
deploy, and each repo's docs) are where a change ships half-wired. Read this
before adding a feature.

## Work from the hub repo for cross-repo features

Run your coding agent from the hub repo (`snaphouse`), with the sibling repos
checked out alongside it (`../nightstand`). Working from the hub gives the agent the
whole-system view and the seams map. If a sibling is not present, clone it into
the parent directory:

```
git clone git@github.com:doyled-it/snaphouse.git
git clone git@github.com:doyled-it/nightstand.git
```

## Cover the full cross-repo surface (definition of done)

Map the change's cross-repo surface at design time, and verify every affected
slice (including docs) at finish time, before opening a merge/pull request. The
request template checklist captures this. The repo map, the seams, and the full
definition of done are in `snaphouse/docs/cross-repo-features.md`.
