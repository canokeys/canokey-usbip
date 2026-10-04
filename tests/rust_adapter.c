/* SPDX-License-Identifier: Apache-2.0 */
/* FIFO adapter regression with a fake DCD; no applet or HID acceptance. */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define EP_NUM 4
#define EP_TX_BUFFER_MAXSIZE 4096
#define USBD_EP_TYPE_CTRL 0x00
#define USBD_EP_TYPE_BULK 0x02
#define USBD_EP_TYPE_INTR 0x03

/* USB/IP bodies exclude the four-byte command word. */
struct CmdSubmitBody {
  uint32_t seq_num, dev_id, direction, ep, transfer_flags;
  uint32_t transfer_buffer_length, start_frame, number_of_packets, interval;
  uint8_t setup[8];
};
struct RetSubmitBody {
  uint32_t seq_num, dev_id, direction, ep, status, actual_length;
  uint32_t start_frame, number_of_packets, error_count;
  uint8_t setup[8];
};
struct Endpoint {
  uint8_t type, mps, host_ready;
  uint8_t *rx_buffer;
  uint16_t rx_size;
  struct CmdSubmitBody intr_in;
};
static struct Endpoint endpoints[EP_NUM];
static unsigned delivered, completions;
static uint32_t tick;

static int write_exact(int fd, const uint8_t *bytes, size_t length) {
  while (length) {
    ssize_t count = write(fd, bytes, length);
    if (count <= 0) return -1;
    bytes += count;
    length -= count;
  }
  return 0;
}
static int read_exact(int fd, uint8_t *bytes, size_t length) {
  while (length) {
    ssize_t count = read(fd, bytes, length);
    if (count <= 0) return -1;
    bytes += count;
    length -= count;
  }
  return 0;
}
static void endpoint_mark_ready(uint32_t ep, uint8_t ready) {
  endpoints[ep].host_ready = ready;
}
static uint32_t device_get_tick(void) { return tick++; }
static void ck_host_usbip_loop(void) {}
static void ck_usb_out(uint8_t ep, const uint8_t *bytes, uint16_t length) {
  (void)ep; (void)bytes; (void)length;
  ++delivered;
}
static void ck_usb_in(uint8_t ep) { (void)ep; ++completions; }
static void ck_usb_setup(const uint8_t *bytes, uint16_t length);
static void ck_usb_boot_reset(void) {}
#include "../Src/rust_adapter.inc"
static void ck_usb_setup(const uint8_t *bytes, uint16_t length) {
  (void)bytes; (void)length;
  rust_packet_length[0] = 0;
  rust_pending[0] = 1;
}

static struct RetSubmitBody reply(int fd) {
  uint32_t command;
  struct RetSubmitBody body;
  assert(read_exact(fd, (uint8_t *)&command, sizeof(command)) == 0);
  assert(ntohl(command) == USBIP_SUBMIT_REPLY);
  assert(read_exact(fd, (uint8_t *)&body, sizeof(body)) == 0);
  return body;
}

int main(void) {
  signal(SIGPIPE, SIG_IGN);
  int sockets[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
  rust_attach();
  ck_usb_dcd_open(0x82);
  struct CmdSubmitBody request = {0};
  request.ep = htonl(2);
  request.direction = htonl(1);
  request.transfer_buffer_length = htonl(RUST_DATA_PACKET_BYTES);
  request.seq_num = htonl(10);
  assert(write_exact(sockets[0], (uint8_t *)&request, sizeof(request)) == 0);
  assert(rust_submit(sockets[1]) == 0);
  request.seq_num = htonl(11);
  assert(write_exact(sockets[0], (uint8_t *)&request, sizeof(request)) == 0);
  assert(rust_submit(sockets[1]) == 0);
  struct RetSubmitBody response = reply(sockets[0]);
  assert(ntohl(response.seq_num) == 11 && (int32_t)ntohl(response.status) == -EBUSY);
  assert(ntohl(endpoints[2].intr_in.seq_num) == 10 && endpoints[2].host_ready);
  const uint8_t payload[] = {0x42};
  assert(ck_usb_dcd_write(0x82, payload, sizeof(payload)) == 1);
  assert(rust_complete_interrupt(sockets[1]) == 0);
  response = reply(sockets[0]);
  uint8_t byte;
  assert(read_exact(sockets[0], &byte, sizeof(byte)) == 0 && byte == payload[0]);
  assert(ntohl(response.seq_num) == 10 && ntohl(response.status) == 0);

  ck_usb_dcd_open(3);
  ck_usb_dcd_stall(3, 1);
  request.ep = htonl(3);
  request.direction = 0;
  request.seq_num = htonl(12);
  request.transfer_buffer_length = htonl(sizeof(payload));
  assert(write_exact(sockets[0], (uint8_t *)&request, sizeof(request)) == 0);
  assert(write_exact(sockets[0], payload, sizeof(payload)) == 0);
  assert(rust_submit(sockets[1]) == 0);
  response = reply(sockets[0]);
  assert((int32_t)ntohl(response.status) == -EPIPE && response.actual_length == 0);
  assert(delivered == 0);
  ck_usb_dcd_stall(3, 0);
  request.seq_num = htonl(13);
  assert(write_exact(sockets[0], (uint8_t *)&request, sizeof(request)) == 0);
  assert(write_exact(sockets[0], payload, sizeof(payload)) == 0);
  assert(rust_submit(sockets[1]) == 0);
  response = reply(sockets[0]);
  assert(ntohl(response.status) == 0 && delivered == 1);

  endpoints[2].host_ready = 1;
  rust_pending[2] = 1;
  rust_packet[2][0] = 0x7f;
  close(sockets[0]);
  assert(rust_complete_interrupt(sockets[1]) == -1);
  rust_disconnect();
  assert(!rust_attached && !endpoints[2].host_ready && !rust_pending[2]);
  assert(rust_packet[2][0] == 0 && endpoints[2].intr_in.seq_num == 0);
  unsigned before = completions;
  assert(rust_complete_interrupt(sockets[1]) == 0 && completions == before);
  close(sockets[1]);
  puts("Rust FIFO adapter: duplicate request, halted OUT and connection ownership passed");
  return 0;
}
