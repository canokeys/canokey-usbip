## CanoKey USB/IP

For developing and user playing, a virtual canokey is implemented based on USB/IP.

Use the following commands to compile and you would find a `canokey-usbip` there.
```
cd build
cmake ..
```

Usage:
```
canokey-usbip [canokey-file [port [touch]]]
```

- `canokey-file`: the file system of the virtual canokey, default value: `/tmp/canokey-file`
- `port`: the port where usbip server listens on, default value 3240. Currently only localhost is supported.
- `touch`: if presents, you could use `Ctrl-C` to issue an touch. Otherwise touch is ignored by the firmware.
