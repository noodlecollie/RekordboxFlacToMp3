import xml.etree.ElementTree as ET
import os
import copy
import argparse
import subprocess
import sys

LOCALHOST_PREFIX = 'file://localhost/'

def parse_args():
	parser = argparse.ArgumentParser(
		'rekordboxFLAC2MP3',
		description='Converts FLAC files in a Rekordbox library to MP3s'
	)

	parser.add_argument(
		'-i',
		'--input',
		required=True,
		help='Input XML file to parse.'
	)

	parser.add_argument(
		'-o',
		'--output',
		help='Output XML file to write. If not specified, defaults to input XML file name with "_new" appended.'
	)

	parser.add_argument(
		'-d',
		'--dry-run',
		action='store_true',
		help='Parses library XML, but does not convert any files or write new XML.'
	)

	parser.add_argument(
		'--ffmpeg',
		help='Path to ffmpeg. If not specified, it is assumed that ffmpeg is present in the system path.'
	)

	return parser.parse_args()

# The escape values didn't quite match up in the original script,
# but I'm gonna leave them as-is for now.
def unescape_from_xml(input: str):
	return input.replace('%20', ' ').replace('%26', '&').replace('%27', "'")

def escape_for_xml(input: str):
	return input.replace(' ', '%20').replace('&', '%26').replace(",", "%27")

# convert FLAC at inFlac path to 320 kpbs mp3 at outmp3 path
def ffmpegFLAC2MP3(inFlac, outmp3, prog_args):
	print(f"Converting {inFlac} to {outmp3}")

	invocation = [
		prog_args.ffmpeg if prog_args.ffmpeg else "ffmpeg",
		"-i", inFlac,
		"-ab", "320k",
		"-map_metadata", "0",
		"-id3v2_version", "3",
		outmp3,
		"-nostdin"
	]

	result = subprocess.run(invocation, capture_output=True, shell=(not prog_args.ffmpeg))

	if result.returncode != 0:
		print("Conversion failed")
		print("Stdout:", result.stdout)
		print("Stderr:", result.stderr)

def main():
	# Currently I don't have a Mac to test this on, and I suspect that Rekordbox
	# does not use the same "file://localhost/" prefix when exporting on a Mac.
	# If that requirement can be verified, the rest of the functionality here
	# should be portable, so it shouldn't be difficult to get it working.
	if sys.platform != "win32":
		print("TODO: Mac support is not yet implemented.")
		sys.exit(1)

	args = parse_args()

	if not args.output:
		args.output = os.path.splitext(args.input)[0] + '_new.xml'

	xmlTree = ET.parse(args.input)
	root = xmlTree.getroot()

	# parse the playlists into a dict with playlist names as keys and lists of track ids as values
	playlists = root[2][0]
	origPlaylistNames = []
	origPlaylistIdLists = []

	for node in playlists:
		pname = node.get('Name')
		origPlaylistNames.append(pname)
		pids = []

		for track in node:
			pids.append(track.get('Key'))

		origPlaylistIdLists.append(pids)

	print('Found', len(origPlaylistNames), 'playlists')

	# make a new playlist with '_MP3' appended to the playlist name if it does not exist
	for pname in origPlaylistNames:
		# if this is already an mp3 playlist do nothing
		if pname.endswith('_MP3'):
			continue

		mp3name = pname + '_MP3'

		# if mp3 version of this playlist exists do nothing
		if mp3name in origPlaylistNames:
			continue

		print(f'Creating new playlist: "{mp3name}"')
		searchStr = "*/[@Name='" + pname + "']"
		origPL = playlists.find(searchStr)

		# add this playlist (NODE in rekordbox notation) to the element tree with an empty tracklist
		mp3list = ET.SubElement(playlists, 'NODE')
		mp3list.set('Name', mp3name)
		mp3list.set('Entries', str(origPL.get('Entries')))
		mp3list.set('Key', str(origPL.get('Key')))
		mp3list.set('Type', str(origPL.get('Type')))

	# navigate to the COLLECTIONS tag and iterate through tracks of the collection
	# the root will be DJ_PLAYLISTS
	# the second child will be collection
	collection = root[1]

	# get the playlist nodes in a list for convenience
	pNodes = root[2][0].findall('*')

	flacToMp3 = {}

	# track id at which to add a new track. Amount of existing entries +1. Incremented every new track created
	currId = int(collection.get('Entries')) + 1

	for track in collection:
		rawPath = track.get('Location')

		# skip file if not a flac
		if not os.path.splitext(rawPath)[1].lower() == '.flac':
			continue

		# get path in python parseable format
		flacPath = unescape_from_xml(rawPath)

		if flacPath.startswith(LOCALHOST_PREFIX):
			flacPath = flacPath[len(LOCALHOST_PREFIX):]

		if not os.path.isfile(flacPath):
			print(f'Could not locate "{flacPath}" on disk, skipping')
			continue

		# get the original track id to figure out what playlists the new mp3 will need to be added to
		# don't convert if it isn't in any playlists to save time
		inPlaylist = False
		origId = track.get('TrackID')

		for pl in pNodes:
			searchStr = "*/[@Key='" + origId + "']"
			result = pl.findall(searchStr)

			if not result:
				continue

			inPlaylist = True
			pname = pl.get('Name')

			# find the mp3 version of the playlist and append the new mp3 id to it
			mp3listName = pname + '_MP3'
			searchStr = "*/[@Name='" + mp3listName + "']"
			mp3list = playlists.find(searchStr)
			newTrack = ET.SubElement(mp3list, 'TRACK')
			newTrack.set('Key', str(currId))

		if not inPlaylist:
			continue

		# at this point, the file was found in at least one playlist
		# convert the file if mp3 doesn't exist already
		mp3Path = os.path.splitext(flacPath)[0] + '.mp3'

		# convert the flac to a 320 kpbs mp3
		flacToMp3[flacPath] = mp3Path

		# copy the old xml track entry and modify the necessary fields
		newTrack = copy.deepcopy(track)
		newTrack.set('TrackID', str(currId))
		newTrack.set('Location', LOCALHOST_PREFIX + escape_for_xml(mp3Path))
		newTrack.set('Kind', "MP3 File")
		newTrack.set('BitRate', "320")
		collection.append(newTrack)

		# increment the current song id number
		currId += 1

	collection.set('Entries', str(currId-1))

	print("Found", len(flacToMp3), "FLAC files to convert to MP3:")

	for flacName in flacToMp3:
		print(f"  {flacName}")

	# TODO: Multi-thread this
	if not args.dry_run:
		for flacName in flacToMp3:
			ffmpegFLAC2MP3(flacName, flacToMp3[flacName], args)

	if not args.dry_run:
		print("Writing new library XML:", args.output)
		xmlTree.write(args.output)

if __name__ == '__main__':
	main()
