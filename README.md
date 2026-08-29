# adado-cli

One-command installer for [AdaDo](https://adado.diginoz.com.au) — your self-hosted AI suite.

## Linux / macOS

```bash
curl -fsSL https://adado.diginoz.com.au/install.sh | bash
```

## Windows (PowerShell)

```powershell
irm https://adado.diginoz.com.au/install.ps1 | iex
```

## What it does

1. Checks Docker is installed (installs if missing)
2. Clones the AdaDo app manifests
3. Starts the AdaDo core (database, proxy, agent coordinator)
4. Opens the AdaDo App Store at `http://localhost/store`

Pick which of the 16 apps you want. Each installs in ~30 seconds with its own AI agent pre-configured.

## More

- [AdaDo apps](https://github.com/diginoz-com-au/adado-apps)
- [Website](https://adado.diginoz.com.au)
- Built by [Diginoz](https://diginoz.com.au) — Apache 2.0
