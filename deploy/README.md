# deploy

Everything that sets up and configures the box, and the package of the library for the NAS.

| Path | Content |
| --- | --- |
| `ansible/` | the Ansible playbook that sets up the box: `site.yaml`, `inventory.yaml`, `group_vars/`, `roles/`; `test/run.sh` tries the role `slots` on a stand-in for a box |
| `config/tinyjukebox.yaml` | config of the box |
| `config/asound.conf` | `/etc/asound.conf` of the box (software volume control `Softmaster`) |
| `config/tinyjukebox-sim.yaml`, `config/library-sim.yaml` | configs for box and library on the PC (`../start_sim.sh`, `../start_library.sh`) |
| `slot/` | what decides which version of the box software runs: `tinyjukebox-slot`, the start script `tinyjukebox-start`, `tinyjukebox-functions`, and the systemd unit that starts the start script |
| `release.pub` | public key that a box verifies the slot archive of a release with |
| `scripts/tinyjukebox.service` | systemd unit of a box without slots |
| `scripts/backup_json.sh` | backup of the JSON files before every start of the service, on a box without slots |
| `scripts/install_service.sh` | installs the unit by hand; not used by the playbook |
| `synology/` | the package of the library for the Synology NAS: `INFO`, `conf/`, `WIZARD_UIFILES/` (the installation dialog), `scripts/` (called by DSM) and `package/` (what lands on the NAS) |

Run from the root of the clone:

```bash
./update_box.sh                    # the playbook: sets the box up and brings its config
./fetch_release.sh                 # the files of a release into dist/: slot archive for a box, package for the NAS
./build_package.sh                 # builds the package for the NAS in dist/; installed by hand in DSM
./update_library.sh NAS            # installs the package of the newest release on the NAS, through the web API of DSM
./release.sh NAS                   # checks, pushes, moves master forward, waits for the release, update_library.sh
```

Documentation:

- [Deployment](../docs/developer/deployment.md): how software and setup reach the box, playbook, roles, variables,
  first setup, updates, changes on the box, data of the previous box, acceptance checklist
- [The box](../docs/developer/box.md): the system the playbook produces, with reasons
- [Operations](../docs/developer/operations.md): service, logs, users, diagnostics
- [Configuration](../docs/developer/configuration.md): all keys of `tinyjukebox.yaml`
- [Library](../docs/developer/library.md): the package for the NAS (section 3.2), how a package is written
  (section 3.6) and its first installation (section 3.7); slots and self-update of a box (section 6)
