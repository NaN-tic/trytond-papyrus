# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import subprocess


class Box(object):

    def __init__(self):
        self.text = None
        self.position = None
        self.size = None
        self.dataCodewords = None
        self.errorCodewordsd = None
        self.dataRegions = None
        self.interleavedBlocks = None
        self.rotationAngle = None
        self.box = None


class DataMatrix:

    @staticmethod
    def scan(filename):
        content = DataMatrix.spawn('dmtxread', '--newline', '--verbose',
            '--milliseconds=10000', filename)
        return DataMatrix.parse_output(content)

    @staticmethod
    def spawn(command, *args):
        # Spawn process and return STDOUT
        command = [command] + list(args)
        process = subprocess.Popen(command , stdout=subprocess.PIPE)
        content = process.communicate()[0]
        return content

    @staticmethod
    def parse_output(content):
        # Each datamatrix is a line of the output
        nextText = False
        box = None
        lines = content.splitlines()
        boxes = []

        for x in range(len(lines)):
            line = lines[x].decode("utf-8")
            if not box and line == ('-' * 50):
                continue

            if not box:
                box = Box()
                boxes.append( box )

            if nextText:
                box.text = line
                nextText = False
                box = None
                continue
            if line == ('-' * 50):
                nextText = True
                continue

            key, value = line.split(':')
            value = value.strip()
            if 'Matrix Size' in key:
                box.size = value
            elif 'Data Codewords' in key:
                box.dataCodewords = value
            elif 'Error Codewords' in key:
                box.errorCodewords = value
            elif 'Data Regions' in key:
                box.dataRegions = value
            elif 'Interleaved Blocks' in key:
                box.interleavedBlocks = value
            elif 'Rotation Angle' in key:
                box.rotationAngle = value
            elif 'Corner 0' in key:
                box.corner0 = value
            elif 'Corner 1' in key:
                box.corner1 = value
            elif 'Corner 2' in key:
                box.corner2 = value
            elif 'Corner 3' in key:
                box.corner3 = value

        return boxes
