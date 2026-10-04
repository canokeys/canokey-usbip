/* SPDX-License-Identifier: Apache-2.0 */
/* USB/IP supplies FIFOs only. Rust owns USB control, CCID, APDUs and storage. */
#define RUST_EP0_PACKET_BYTES 16
#define RUST_DATA_PACKET_BYTES 64
#define RUST_KEYBOARD_PACKET_BYTES 8
#define USBIP_SUBMIT_REPLY 0x03
#define USBIP_REQUEST_TIMEOUT_MS 10000
static uint8_t rust_rx[EP_NUM][RUST_DATA_PACKET_BYTES];
static uint8_t rust_pending[EP_NUM], rust_halted[EP_NUM][2];
static uint16_t rust_packet_length[EP_NUM];
static uint8_t rust_packet[EP_NUM][RUST_DATA_PACKET_BYTES];
static void (*rust_timer)(void);
static uint32_t rust_deadline;
static volatile sig_atomic_t rust_touch_requested;
int ck_host_stopping(void) { return 0; }
void device_set_timeout(void (*callback)(void), uint16_t milliseconds) {
  rust_timer = callback;
  rust_deadline = device_get_tick() + milliseconds;
}
void ck_usb_dcd_start(void) {}
void ck_usb_dcd_enable_irq(void) {}
void ck_usb_dcd_stop(void) { memset(rust_pending, 0, sizeof(rust_pending)); }
void ck_usb_dcd_open(uint8_t endpoint) {
  unsigned ep = endpoint & 0x7F;
  assert(ep < EP_NUM);
  endpoints[ep].type = ep == 0 ? USBD_EP_TYPE_CTRL : ep == 3 ? USBD_EP_TYPE_BULK : USBD_EP_TYPE_INTR;
  endpoints[ep].mps = ep == 0 ? RUST_EP0_PACKET_BYTES : ep == 1 ? RUST_KEYBOARD_PACKET_BYTES : RUST_DATA_PACKET_BYTES;
  endpoints[ep].rx_buffer = rust_rx[ep];
  endpoints[ep].rx_size = endpoints[ep].mps;
  rust_halted[ep][endpoint >> 7] = 0;
}
void ck_usb_dcd_close(uint8_t endpoint) {
  if (endpoint & 0x80) rust_pending[endpoint & 0x7F] = 0;
}
void ck_usb_dcd_stall(uint8_t endpoint, uint8_t halt) {
  rust_halted[endpoint & 0x7F][endpoint >> 7] = halt;
}
void ck_usb_dcd_address(uint8_t address) { (void)address; }
void ck_usb_dcd_ready(uint8_t ready) { (void)ready; }
void ck_usb_dcd_receive(uint8_t endpoint) {
  endpoints[endpoint & 0x7F].rx_size = endpoints[endpoint & 0x7F].mps;
}
uint8_t ck_usb_dcd_write(uint8_t endpoint, const uint8_t *bytes, uint16_t length) {
  unsigned ep = endpoint & 0x7F;
  assert(ep < EP_NUM && length <= endpoints[ep].mps && !rust_pending[ep]);
  if (length) memcpy(rust_packet[ep], bytes, length);
  rust_packet_length[ep] = length;
  rust_pending[ep] = 1;
  return 1;
}
static void compat_device_loop(void) {
  if (rust_touch_requested) {
    rust_touch_requested = 0;
    FILE *presence = fopen("/tmp/canokey-test-up", "w");
    if (presence) { fputs("0\n", presence); fclose(presence); }
  }
  if (rust_timer && (int32_t)(device_get_tick() - rust_deadline) >= 0) {
    void (*callback)(void) = rust_timer;
    rust_timer = NULL;
    callback();
  }
  ck_host_usbip_loop();
}
static void rust_out(uint8_t ep, const uint8_t *bytes) {
  ck_usb_out(ep, bytes, endpoints[ep].rx_size);
  compat_device_loop();
}
static void sigint_handler(int signal_number) {
  (void)signal_number;
  /* Defer host presence changes until serialized main-loop execution. */
  rust_touch_requested = 1;
}
static int rust_complete_interrupt(int client_fd) {
  for (unsigned ep = 1; ep < EP_NUM; ++ep) {
    if (!endpoints[ep].host_ready || !rust_pending[ep]) continue;
    unsigned count = rust_packet_length[ep];
    if (count > ntohl(endpoints[ep].intr_in.transfer_buffer_length)) return -1;
    struct RetSubmitBody response = {0};
    response.seq_num = endpoints[ep].intr_in.seq_num;
    response.actual_length = htonl(count);
    response.start_frame = htonl(UINT32_MAX);
    const uint8_t command[4] = {0, 0, 0, USBIP_SUBMIT_REPLY};
    if (write_exact(client_fd, command, sizeof(command)) < 0 ||
        write_exact(client_fd, (uint8_t *)&response, sizeof(response)) < 0 ||
        write_exact(client_fd, rust_packet[ep], count) < 0) return -1;
    endpoints[ep].host_ready = 0;
    rust_pending[ep] = 0;
    ck_usb_in(ep | 0x80);
  }
  return 0;
}
static int rust_submit(int client_fd) {
  struct CmdSubmitBody request;
  if (read_exact(client_fd, (uint8_t *)&request, sizeof(request)) < 0) return -1;
  uint32_t ep = ntohl(request.ep);
  uint32_t capacity = ntohl(request.transfer_buffer_length);
  uint32_t direction = ntohl(request.direction);
  if (ep >= EP_NUM || (direction == 0 && capacity > EP_TX_BUFFER_MAXSIZE) || direction > 1) {
    fprintf(stderr, "Rust USB/IP rejected submit: ep=%u capacity=%u direction=%u\n", ep, capacity, direction);
    return -1;
  }
  if (ep != 0 && endpoints[ep].mps == 0) {
    fprintf(stderr, "Rust USB/IP submit to unopened endpoint %u\n", ep);
    return -1;
  }
  /* libccid can request its maximum receive buffer. A short device reply
   * uses our bounded staging buffer without allocating the host capacity. */
  if (direction == 1 && capacity > EP_TX_BUFFER_MAXSIZE) capacity = EP_TX_BUFFER_MAXSIZE;
  struct RetSubmitBody response = {0};
  response.seq_num = request.seq_num;
  response.start_frame = htonl(UINT32_MAX);
  uint8_t bytes[EP_TX_BUFFER_MAXSIZE];
  uint32_t length = 0;
  if (ep != 0 && endpoints[ep].type == USBD_EP_TYPE_INTR && direction == 1 && !rust_pending[ep]) {
    if (endpoints[ep].host_ready) return -1;
    endpoints[ep].intr_in = request;
    endpoint_mark_ready(ep, 1);
    return 0;
  }
  if (ep == 0) {
    rust_pending[0] = 0;
    ck_usb_setup(request.setup, sizeof(request.setup));
  }
  if (direction == 0) {
    while (length < capacity) {
      unsigned count = capacity - length;
      if (count > endpoints[ep].mps) count = endpoints[ep].mps;
      if (read_exact(client_fd, bytes, count) < 0) return -1;
      ck_usb_out(ep, bytes, count);
      compat_device_loop();
      length += count;
    }
    if (ep == 0 && rust_pending[0]) {
      rust_pending[0] = 0;
      ck_usb_in(0x80);
    }
  } else {
    uint32_t started = device_get_tick();
    while (length < capacity) {
      compat_device_loop();
      if (rust_halted[ep][1]) {
        response.status = htonl(-EPIPE);
        break;
      }
      if (!rust_pending[ep]) {
        if (device_get_tick() - started > USBIP_REQUEST_TIMEOUT_MS) {
          response.status = htonl(-ETIMEDOUT);
          break;
        }
        usleep(1000);
        continue;
      }
      unsigned count = rust_packet_length[ep];
      if (count > capacity - length) return -1;
      memcpy(bytes + length, rust_packet[ep], count);
      length += count;
      rust_pending[ep] = 0;
      ck_usb_in(ep | 0x80);
      if (count < endpoints[ep].mps) break;
    }
    if (ep == 0) ck_usb_out(0, NULL, 0);
  }
  response.actual_length = htonl(length);
  const uint8_t command[4] = {0, 0, 0, USBIP_SUBMIT_REPLY};
  if (write_exact(client_fd, command, sizeof(command)) < 0 ||
      write_exact(client_fd, (uint8_t *)&response, sizeof(response)) < 0) return -1;
  return direction == 1 ? write_exact(client_fd, bytes, length) : 0;
}
