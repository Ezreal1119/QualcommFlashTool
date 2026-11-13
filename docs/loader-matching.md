# Programmer discovery and matching

Firmware files are checked offline first. Uploading a programmer happens only
inside the explicit device check or confirmed flash workflow. No AI or network
service is involved. Editing files remains supported; edits require another check.

## Selection rules

1. Discover all non-hidden `.elf` / `.mbn` files in the selected folder, using the
   existing bounded recursive scan. Filenames, `ddr`, CPU aliases and directory
   names do not establish compatibility. At most 128 binaries are inspected.
2. Parse supported ARM ELF32/64 structures and Qualcomm signing metadata. A
   programmer must declare `SW_ID=3`. Verify its certificate signatures, image
   signature and every ELF segment hash, including coverage of the ELF headers.
   Invalid or unsupported binaries are excluded with reasons in the package report.
   They are never uploaded automatically. If no supported programmer is found,
   preparation stops.
3. Freeze every supported candidate's path and SHA-256 in the execution plan.
   The review lists candidates as **pending hardware matching**, even with one file.
4. After confirmation (or explicit device inspection), read Sahara HWID, the
   **complete** root-certificate hash and chip serial. Compare root SHA-256 or
   SHA-384, OEM/model, the signing hardware constraint and any serial allow-list.
   Never use a hash prefix, an unfused wildcard or filename-based fallback.
5. Upload only when one distinct programmer matches and no candidate remains
   unconfirmed. Byte-identical copies count as one programmer. Conflicts are
   excluded; two different matching programmers or an unknown candidate block
   upload and require a reviewed manual selection. Manual selection narrows the
   candidate set; it does **not** bypass signature or hardware checks.
6. Upload a private temporary snapshot with the verified SHA-256. An edit between
   inspection and transfer cannot replace its contents. Snapshots are reclaimed
   after upload and are not included in diagnostic exports.

The console reports the selected path and evidence. `loader_selection.json` and
the task's events retain the candidate identities, hashes and reasons. Firmware
model compatibility is still the user's responsibility; programmer authentication
does not establish that the entire firmware belongs on that model. The device's
secure boot implementation remains the authority for accepting the upload.

## Supported layouts and limits

The first implementation supports little-endian ARM/AArch64 executable ELF files
with one Qualcomm single-OEM MBN-v6 signing segment, 0.0 metadata, nonpaged
segments, RSA certificate/signature chains and SHA-256/SHA-384 segment tables.
The `.mbn` extension is accepted when its contents use this ELF layout.
Flat legacy MBN, MBN v3/v5/v7, dual-signing, paged layouts, missing device identity,
unrecognized signing restrictions and zero root hashes are rejected explicitly.
This is not a claim of universal Qualcomm loader compatibility.

Certificate verification here follows the programmer's signed-image structure,
not HTTPS/web PKI: certificate signatures and issuer CA constraints are checked;
wall-clock validity and online revocation are not. Some real Qualcomm leaves use
the non-RFC `CA:FALSE/pathlen:0` encoding; leaf extensions are not interpreted as
web-PKI authorization. Root trust comes from the full hash read from the device,
not from the host certificate store or a self-signed certificate alone.

A SoC-bound signature (`in_use_soc_hw_version`) cannot safely be converted to an
MSM HWID by looking up a CPU name. Sahara v2 does not supply the required SoC
version; such cases are unconfirmed and blocked unless narrowly recorded hardware
evidence covers the exact combination. The only initial exception is the exact
programmer **and chip** from the earlier DT50 test:

- Loader SHA-256: `147b7cf50f27d4feced8563aec47483cf84557da5db7bf1581942cbca073e908`.
- HWID: `0014d0e100000000`; chip serial: `688bc49b`; signed SoC version: `9002`.
- Root SHA-384: `52ce9c0eba3d6712cb2594d1d6993067ef148b3bb5ac068937bbfe8661176bb06c185f733459b7b2262692031b349a90`.

This evidence is tied to [the recorded DT50 run](hardware/DT50-2026-11-12.md)
and subsequent **offline** signature inspection. It is not a mapping for every
SDM662, every DT50, another serial number, or a changed programmer. No new live
upload, flash or reboot was performed while adding this feature.

## Existing RAM programmers

A device already in Firehose cannot provide the same Sahara authentication data.
The tool does not assume that the selected on-disk file is the running programmer.
It may reuse its own previously verified upload for five minutes after the last
successful connection, only when macOS reports the same unique USB session ID,
location, VID/PID and address, and the same programmer still passes the frozen-plan
checks. A reconnect, changed programmer, expired receipt or absent OS evidence
requires re-entering normal EDL/Sahara. Unknown RAM programmers are blocked before
configure or firmware writes.

`loader-session.json` stores this small local workflow receipt in the application
data directory, with no programmer bytes. Reset clears it. It is not remote
attestation or protection against another malicious process running as the user.

## References

- [Qualcomm security profiles](https://github.com/qualcomm/security-profiles/blob/main/talos_security_profile.xml): device-programmer SW ID and ELF-v6 profile.
- [Qualcomm secure boot and image authentication](https://www.qualcomm.com/content/dam/qcomm-martech/dm-assets/documents/secure-boot-and-image-authentication-version_final.pdf): signed-image structure and root-hash trust.
- [PyCA certificate APIs](https://cryptography.io/en/latest/x509/reference/): signature verification primitives.

The controlled EDL source snapshot is unchanged; its automatic loader database
selection is disabled by supplying a nonempty programmer before identification.
