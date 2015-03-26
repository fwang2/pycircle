#!/usr/bin/env python

import os
import logging
import hashlib
import argparse
import stat

from mpi4py import MPI
from cStringIO import StringIO

from circle import Circle
from _version import get_versions
from task import BaseTask
from utils import logging_init, bytes_fmt
from fwalk import FWalk
from cio import readn, writen

logger = logging.getLogger("checksum")
CHUNKSIZE = 134217728   # 512 MiB = 536870912
BLOCKSIZE = 4194304     # 4 MiB

ARGS    = None
__version__ = get_versions()['version']
del get_versions

def parse_args():
    parser = argparse.ArgumentParser(description="fchecksum")
    parser.add_argument("-v", "--version", action="version", version="{version}".format(version=__version__))
    parser.add_argument("--loglevel", default="ERROR", help="log level")
    parser.add_argument("path", default=".", help="path")
    parser.add_argument("-i", "--interval", type=int, default=10, help="interval")

    return parser.parse_args()

class Chunk:
    def __init__(self, filename, off_start=0, length=0):
        self.filename = filename
        self.off_start = off_start
        self.length = length
        self.digest = None



class Checksum(BaseTask):
    def __init__(self, circle, treewalk, totalsize=0):
        global logger
        BaseTask.__init__(self, circle)
        self.circle = circle
        self.treewalk = treewalk
        self.totalsize = totalsize
        self.workcnt = 0
        self.chunkq = []

        # debug
        self.d = {"rank": "rank %s" % circle.rank}

        # reduce
        self.vsize = 0

        if self.circle.rank == 0:
            print("Start parallel checksumming ...")


    def create(self):

        #if self.workq:  # restart
        #    self.setq(self.workq)
        #    return

        for f in self.treewalk.flist:
            if stat.S_ISREG(f[1]):
                self.enq_file(f)

        # right after this, we do first checkpoint

        #if self.checkpoint_file:
        #    self.do_no_interrupt_checkpoint()
        #    self.checkpoint_last = MPI.Wtime()

    def enq_file(self, f):
        '''
        f[0] path f[1] mode f[2] size - we enq all in one shot
        CMD = copy src  dest  off_start  last_chunk
        '''
        chunks    = f[2] / CHUNKSIZE
        remaining = f[2] % CHUNKSIZE


        workcnt = 0

        if f[2] == 0: # empty file
            ck = Chunk(f[0])
            ck.off_start = 0
            ck.length = 0
            self.enq(ck)
            logger.debug("%s" % ck, extra=self.d)
            workcnt += 1
        else:
            for i in range(chunks):
                ck = Chunk(f[0])
                ck.off_start = i * CHUNKSIZE
                ck.length = CHUNKSIZE
                self.enq(ck)
                logger.debug("%s" % ck, extra=self.d)
            workcnt += chunks

        if remaining > 0:
            # send remainder
            ck = Chunk(f[0])
            ck.off_start = chunks * CHUNKSIZE
            ck.length  = remaining
            self.enq(ck)
            logger.debug("%s" % ck, extra=self.d)
            workcnt += 1

        # tally work cnt
        self.workcnt += workcnt



    def process(self):
        ck = self.deq()
        logger.debug("process: %s" % ck, extra = self.d)
        blocks = ck.length / BLOCKSIZE
        remaining = ck.length % BLOCKSIZE

        chunk_digests = StringIO()

        fd = os.open(ck.filename, os.O_RDONLY)
        os.lseek(fd, ck.off_start, os.SEEK_SET)

        for i in range(blocks):
            chunk_digests.write(hashlib.sha1(readn(fd, BLOCKSIZE)).hexdigest())

        if remaining > 0:
            chunk_digests.write(hashlib.sha1(readn(fd, remaining)).hexdigest())

        ck.digest = chunk_digests.getvalue()

        self.chunkq.append(ck)

        self.vsize += ck.length
        os.close(fd)

    def setLevel(self, level):
        global logger
        logging_init(logger, level)


    def reduce_init(self, buf):
        buf['vsize'] = self.vsize

    def reduce_report(self, buf):
        out = ""
        if self.totalsize != 0:
            out += "%.2f %% verified, " % (100 * float(buf['vsize'])/self.totalsize)

        out += "%s bytes done" % bytes_fmt(buf['vsize'])
        print(out)

    def reduce_finish(self, buf):
        #self.reduce_report(buf)
        pass

    def reduce(self, buf1, buf2):
        buf1['vsize'] += buf2['vsize']
        return buf1


def main():

    global ARGS, logger
    ARGS = parse_args()
    root = os.path.abspath(ARGS.path)
    circle = Circle(reduce_interval = ARGS.interval)
    logger = logging_init(logger, ARGS.loglevel)

    fwalk = FWalk(circle, root)
    circle.begin(fwalk)
    circle.finalize()
    totalsize = fwalk.epilogue()

    fcheck = Checksum(circle, fwalk, totalsize)
    fcheck.setLevel(ARGS.loglevel)
    circle.begin(fcheck)
    circle.finalize()


if __name__ == "__main__": main()