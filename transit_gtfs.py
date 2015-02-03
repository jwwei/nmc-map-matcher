"""
transit_gtfs.py outputs a series of CSV files in the current path that
    are used for VISTA analysis of transit paths. In short, map-matched
    transit paths are the basis for map-matched bus stops. These stops
    are mapped to the underlying VISTA network, but because of a
    limitation in VISTA, only one of these stops may be mapped to any
    one link. (Extra stops for the time being are dropped).
@author: Kenneth Perrine
@contact: kperrine@utexas.edu
@organization: Network Modeling Center, Center for Transportation Research,
    Cockrell School of Engineering, The University of Texas at Austin 
@version: 1.0

@copyright: (C) 2014, The University of Texas at Austin
@license: GPL v3

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
from __future__ import print_function
from nmc_mm_lib import gtfs, vista_network, path_engine, graph
import problem_report, sys, time
from datetime import datetime, timedelta

DWELLTIME_DEFAULT = 0
"@var DWELLTIME_DEFAULT is the dwell time to report in the bus_route_link.csv file output."

problemReport = False
"@var problemReport is set to true when the -p parameter is specified."

def syntax():
    """
    Print usage information
    """
    print("transit_gtfs outputs a series of CSV files in the current path that")
    print("are used for VISTA analysis of transit paths.")
    print()
    print("Usage:")
    print("  python transit_gtfs.py dbServer network user password shapePath")
    print("    pathMatchFile -t refDateTime [-e endTime] {[-c serviceID]")
    print("    [-c serviceID] ...} [-p]")
    print()
    print("where:")
    print("  -t is the zero-reference time that all arrival time outputs are related to.")
    print("     (Note that the day is ignored.) Use the format HH:MM:SS.")
    print("  -e is the end time in seconds (86400 by default)")
    print("  -c restricts results to specific service IDs (default: none)")
    print("  -p outputs a problem report on the stop matches")
    sys.exit(0)

def restorePathMatch(dbServer, networkName, userName, password, shapePath, pathMatchFilename):
    # Get the database connected:
    print("INFO: Connect to database...", file = sys.stderr)
    database = vista_network.connect(dbServer, userName, password, networkName)
    
    # Read in the topology from the VISTA database:
    print("INFO: Read topology from database...", file = sys.stderr)
    vistaGraph = vista_network.fillGraph(database)
    
    # Read in the shapefile information:
    print("INFO: Read GTFS shapefile...", file = sys.stderr)
    gtfsShapes = gtfs.fillShapes(shapePath, vistaGraph.gps)

    # Read the path-match file:
    print("INFO: Read the path-match file '%s'..." % pathMatchFilename, file = sys.stderr)
    with open(pathMatchFilename, 'r') as inFile:
        gtfsNodes = path_engine.readStandardDump(vistaGraph, gtfsShapes, inFile)
        "@type gtfsNodes: dict<int, list<path_engine.PathEnd>>"

    # Filter out the unused shapes:
    unusedShapeIDs = set()
    for shapeID in gtfsShapes.keys():
        if shapeID not in gtfsNodes:
            del gtfsShapes[shapeID]
            unusedShapeIDs.add(shapeID)

    return (vistaGraph, gtfsShapes, gtfsNodes, unusedShapeIDs)

def _outHeader(tableName, userName, networkName, outFile):
    print("User,%s" % userName, file = outFile)
    print("Network,%s" % networkName, file = outFile)
    print("Table,public.bus_route", file = outFile)
    print(time.strftime("%a %b %d %H:%M:%S %Y"), file = outFile)
    print(file = outFile)

def dumpBusRoutes(gtfsTrips, userName, networkName, outFile = sys.stdout):
    """
    dumpBusRoutes dumps out a public.bus_route.csv file contents.
    @type gtfsTrips: dict<int, gtfs.TripsEntry>
    @type userName: str
    @type networkName: str
    @type outFile: file
    """
    _outHeader("public.bus_route", userName, networkName, outFile)
    print("\"id\",\"name\",", file = outFile)
    
    tripIDs = gtfsTrips.keys()
    tripIDs.sort()
    for tripID in tripIDs:
        append = ""
        if len(gtfsTrips[tripID].route.name) > 0:
            append = ": " + gtfsTrips[tripID].route.name
        print("\"%d\",\"%s\"" % (tripID, gtfsTrips[tripID].route.shortName + append),
                file = outFile)

class stop_match:
    """
    stop_match represents a stop and a PointOnLink, used within dumpBusRouteLinks.
    """
    def __init__(self, tripID, gtfsNode, stopsEntry, dist):
        """
        @type tripID: int
        @type gtfsNode: path_engine.PathEnd
        @type stopsEntries: gtfs.StopsEntry
        @type dist: float
        """
        self.tripID = tripID
        self.gtfsNode = gtfsNode
        self.stopsEntry = stopsEntry
        self.dist = dist

def dumpBusRouteLinks(gtfsTrips, gtfsNodes, gtfsStopTimes, vistaNetwork, stopSearchRadius, userName, networkName, outFile = sys.stdout):
    """
    dumpBusRouteLinks dumps out a public.bus_route_link.csv file contents.
    @type gtfsTrips: dict<int, gtfs.TripsEntry>
    @type gtfsNodes: dict<int, list<path_engine.PathEnd>>
    @type gtfsStopTimes: dict<TripsEntry, list<StopTimesEntry>>
    @type vistaNetwork: graph.GraphLib
    @type stopSearchRadius: float
    @type userName: str
    @type networkName: str
    @type outFile: file
    @return A mapping of stopID to points-on-links.
    @rtype dict<int, graph.PointOnLink>
    """
    _outHeader("public.bus_route_link", userName, networkName, outFile)
    print('"route","sequence","link","stop","dwelltime",', file = outFile)
    
    # Set up the output:
    ret = {}
    
    # Initialize the path engine for use later:
    pathEngine = path_engine.PathEngine(stopSearchRadius, stopSearchRadius, stopSearchRadius, sys.float_info.max, sys.float_info.max,
                                        stopSearchRadius, 1, 1, 1, sys.maxint, sys.maxint)
    pathEngine.limitClosestPoints = 8
    pathEngine.limitSimultaneousPaths = 6
    pathEngine.maxHops = 12
    pathEngine.logFile = None # Suppress the log outputs for the path engine; enough stuff will come from other sources.

    problemReportNodes = {}
    "@type problemReportNodes: dict<?, path_engine.PathEnd>"
    
    tripIDs = gtfsTrips.keys()
    tripIDs.sort()
    for tripID in tripIDs:
        if gtfsTrips[tripID].shapeEntries[0].shapeID not in gtfsNodes:
            # This happens if the incoming files contain a subset of all available topology.
            continue
        
        print("INFO: Outputting route for trip %d." % tripID, file = sys.stderr)
        
        treeNodes = gtfsNodes[gtfsTrips[tripID].shapeEntries[0].shapeID]
        "@type treeNodes: list<path_engine.PathEnd>"
        
        # Step 1: Find the longest distance of contiguous valid links within the shape for each trip:
        startIndex = -1
        curIndex = 0
        linkCount = 0
        totalLinks = 0
        
        longestStart = -1
        longestEnd = len(treeNodes)
        longestDist = sys.float_info.min
        longestLinkCount = 0
        
        while curIndex <= len(treeNodes):
            if (curIndex == len(treeNodes)) or (curIndex == 0) or treeNodes[curIndex].restart:
                totalLinks += 1
                linkCount += 1
                if (curIndex > startIndex) and (startIndex >= 0):
                    # We have a contiguous interval.  See if it wins:
                    if treeNodes[curIndex - 1].totalDist - treeNodes[startIndex].totalDist > longestDist:
                        longestStart = startIndex
                        longestEnd = curIndex
                        longestDist = treeNodes[curIndex - 1].totalDist - treeNodes[startIndex].totalDist
                        longestLinkCount = linkCount
                        linkCount = 0
                    
                # This happens if it is time to start a new interval:
                startIndex = curIndex
            else:
                totalLinks += len(treeNodes[curIndex].routeInfo)
                linkCount += len(treeNodes[curIndex].routeInfo)
            curIndex += 1

        if longestStart >= 0:
            # We have a valid path.  See if it had been trimmed down and report it.
            if (longestStart > 0) or (longestEnd < len(treeNodes)):
                print("WARNING: For shape ID %d from seq. %d through %d, %.2g%% of %d links will be used." \
                      % (treeNodes[longestStart].shapeEntry.shapeID, treeNodes[longestStart].shapeEntry.shapeSeq,
                         treeNodes[longestEnd - 1].shapeEntry.shapeSeq, 100 * float(longestLinkCount) / float(totalLinks),
                         totalLinks), file = sys.stderr)
            
            # Step 2: Match up stops to that contiguous list:
            stopTimes = gtfsStopTimes[gtfsTrips[tripID]]
            "@type stopTimes: list<gtfs.StopTimesEntry>"
            
            # Isolate the relevant VISTA tree nodes: (Assume from above that this is a non-zero length array)
            ourGTFSNodes = treeNodes[longestStart:longestEnd]
            
            # We are going to recreate a small VISTA network from ourGTFSNodes and then match up the stops to that.
            # First, prepare the small VISTA network:
            vistaSubset = graph.GraphLib(vistaNetwork.gps.latCtr, vistaNetwork.gps.lngCtr)
            vistaNodePrior = None
            "@type vistaNodePrior: graph.GraphNode"
            
            # Build a list of links:
            outLinkIDList = []
            "@type outLinkList: list<int>"
            
            # Plop in the start node:
            vistaNodePrior = graph.GraphNode(ourGTFSNodes[0].pointOnLink.link.origNode.id,
                ourGTFSNodes[0].pointOnLink.link.origNode.gpsLat, ourGTFSNodes[0].pointOnLink.link.origNode.gpsLng)
            vistaSubset.addNode(vistaNodePrior)
            outLinkIDList.append(ourGTFSNodes[0].pointOnLink.link.id)
            
            # Link together nodes as we traverse through them:
            for ourGTFSNode in ourGTFSNodes:
                "@type ourGTFSNode: path_engine.PathEnd"
                # There should only be one destination link per VISTA node because this comes form our tree.
                # If there is no link or we're repeating the first one, then there were no new links assigned.
                if (len(ourGTFSNode.routeInfo) < 1) or ((len(outLinkIDList) == 1) \
                        and (ourGTFSNode.routeInfo[0].id == ourGTFSNodes[0].pointOnLink.link.id)):
                    continue
                for link in ourGTFSNode.routeInfo:
                    "@type link: graph.GraphLink"
                
                    if link.id not in vistaNetwork.linkMap:
                        print("WARNING: In finding bus route links, link ID %d is not found in the VISTA network." % link.id, file = sys.stderr)
                        continue
                    origVistaLink = vistaNetwork.linkMap[link.id]
                    "@type origVistaLink: graph.GraphLink"
                    
                    if origVistaLink.origNode.id not in vistaSubset.nodeMap:
                        # Create a new node:
                        vistaNode = graph.GraphNode(origVistaLink.origNode.id, origVistaLink.origNode.gpsLat, origVistaLink.origNode.gpsLng)
                        vistaSubset.addNode(vistaNode)
                    else:
                        # The path evidently crosses over itself.  Reuse an existing node.
                        vistaNode = vistaSubset.nodeMap[origVistaLink.origNode.id]
                        
                    # We shall label our links as indices into the stage we're at in ourGTFSNodes links.  This will allow for access later.
                    if outLinkIDList[-1] not in vistaSubset.linkMap:
                        vistaSubset.addLink(graph.GraphLink(outLinkIDList[-1], vistaNodePrior, vistaNode))
                    vistaNodePrior = vistaNode
                    outLinkIDList.append(link.id)
                    
            # And then finish off the graph with the last link:
            if ourGTFSNode.pointOnLink.link.destNode.id not in vistaSubset.nodeMap:
                vistaNode = graph.GraphNode(ourGTFSNode.pointOnLink.link.destNode.id, ourGTFSNode.pointOnLink.link.destNode.gpsLat, ourGTFSNode.pointOnLink.link.destNode.gpsLng)
                vistaSubset.addNode(vistaNode)
            if outLinkIDList[-1] not in vistaSubset.linkMap:
                vistaSubset.addLink(graph.GraphLink(outLinkIDList[-1], vistaNodePrior, vistaNode))
            
            # Then, prepare the stops as GTFS shapes entries:
            print("INFO: Mapping stops to VISTA network...", file = sys.stderr)
            gtfsShapes = []
            gtfsStopsLookup = {}
            "@type gtfsStopsLookup: dict<int, gtfs.StopTimesEntry>"
            
            # Append an initial dummy shape to force routing through the path start:
            gtfsShapes.append(gtfs.ShapesEntry(-1, -1, ourGTFSNodes[0].pointOnLink.link.origNode.gpsLat,
                                                ourGTFSNodes[0].pointOnLink.link.origNode.gpsLng))
            
            # Append all of the stops:
            for gtfsStopTime in stopTimes:
                "@type gtfsStopTime: gtfs.StopTimesEntry"
                gtfsShapes.append(gtfs.ShapesEntry(-1, gtfsStopTime.stopSeq, gtfsStopTime.stop.gpsLat, gtfsStopTime.stop.gpsLng))
                gtfsStopsLookup[gtfsStopTime.stopSeq] = gtfsStopTime

            # Append a trailing dummy shape to force routing through the path end:
            gtfsShapes.append(gtfs.ShapesEntry(-1, -1, ourGTFSNodes[-1].pointOnLink.link.destNode.gpsLat,
                                                ourGTFSNodes[-1].pointOnLink.link.destNode.gpsLng))
        
            # Find a path through our prepared node map subset:
            resultTree = pathEngine.constructPath(gtfsShapes, vistaSubset)
            "@type resultTree: list<path_engine.PathEnd>"
            
            # Strip off the dummy ends:
            del resultTree[-1]
            del resultTree[0]
            if len(resultTree) > 0:
                resultTree[0].prevTreeNode = None
            
            # So now we should have one tree entry per matched stop.

            # Deal with Problem Report:
            if problemReport:
                revisedNodeList = {}
                prevNode = None
                "@type revisedNodeList = list<path_engine.PathEnd>"
                for stopNode in resultTree:
                    # Reconstruct a tree node in terms of the original network.
                    newShape = gtfs.ShapesEntry(gtfsTrips[tripID].shapeEntries[0].shapeID,
                        stopNode.shapeEntry.shapeSeq, stopNode.shapeEntry.lat, stopNode.shapeEntry.lng, False)
                    origLink = vistaNetwork.linkMap[stopNode.pointOnLink.link.id] 
                    newPointOnLink = graph.PointOnLink(origLink, stopNode.pointOnLink.dist,
                        stopNode.pointOnLink.nonPerpPenalty, stopNode.pointOnLink.refDist)
                    newNode = path_engine.PathEnd(newShape, newPointOnLink)
                    newNode.restart = False
                    newNode.totalCost = stopNode.totalCost
                    newNode.totalDist = stopNode.totalDist
                    newNode.routeInfo = []
                    for link in stopNode.routeInfo:
                        newNode.routeInfo.append(vistaNetwork.linkMap[link.id])
                    newNode.prevTreeNode = prevNode
                    prevNode = newNode
                    revisedNodeList[stopNode.shapeEntry.shapeSeq] = newNode
                problemReportNodes[gtfsTrips[tripID].shapeEntries[0].shapeID] = revisedNodeList 
        
            # Walk through our output link list and see where the resultTree entries occur:
            resultIndex = 0
            foundStopSet = set()
            "@type foundStopSet: set<int>"
            outSeqCtr = longestStart
            for linkID in outLinkIDList:
                bestTreeEntry = None
                curResultIndex = resultIndex
                "@type bestTreeEntry: path_engine.PathEnd"
                # This routine will advance resultIndex only if a stop is found for linkID, and will exit out when
                # no more stops are found for linkID. 
                matchCtr = 0
                while curResultIndex < len(resultTree):
                    if resultTree[curResultIndex].pointOnLink.link.id == linkID:
                        foundStopSet.add(resultTree[curResultIndex].shapeEntry.shapeSeq) # Check off this stop sequence.
                        if (bestTreeEntry is None) or (resultTree[resultIndex].pointOnLink.refDist < bestTreeEntry.pointOnLink.refDist):
                            bestTreeEntry = resultTree[resultIndex]
                        matchCtr += 1
                        resultIndex = curResultIndex + 1
                    curResultIndex += 1
                    if (matchCtr == 0) or ((curResultIndex < len(resultTree)) and (resultTree[resultIndex].pointOnLink.link.id == linkID)):
                        continue
                    # We have gotten to the end of matched link(s). 
                    break 
                if matchCtr > 1:
                    # Report duplicates:
                    print("WARNING: %d stops have been matched for TripID %d, LinkID %d. Keeping Stop %d, Stop Seq %d" % (matchCtr,
                        tripID, linkID, gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID,
                        bestTreeEntry.shapeEntry.shapeSeq), file = sys.stderr)
                if matchCtr > 0:
                    # Report the best match:
                    print('"%d","%d","%d","%d","%d",' % (tripID, outSeqCtr, linkID,
                        gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID, DWELLTIME_DEFAULT), file = outFile)
                    if gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID in ret \
                            and ret[gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID].link.id \
                                != bestTreeEntry.pointOnLink.link.id:
                        print("WARNING: stopID %d is attempted to be assigned to linkID %d, but it had already been assigned to linkID %d." \
                            % (gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID, bestTreeEntry.pointOnLink.link.id,
                               ret[gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID].link.id), file = sys.stderr)
                    else:
                        ret[gtfsStopsLookup[bestTreeEntry.shapeEntry.shapeSeq].stop.stopID] = bestTreeEntry.pointOnLink                    
                else:
                    # The linkID has nothing to do with any points in consideration.  Report it without a stop:
                    print('"%d","%d","%d",,,' % (tripID, outSeqCtr, linkID), file = outFile)
                outSeqCtr += 1

            # Are there any stops left over?  If so, report them to say that they aren't in the output file.
            for gtfsStopTime in stopTimes:
                "@type gtfsStopTime: gtfs.StopTimesEntry"
                if gtfsStopTime.stopSeq not in foundStopSet:
                    # This stop is unaccounted for:
                    print("WARNING: Trip tripID %d, stopID %d stop seq. %d will not be in the bus_route_link file." % (tripID,
                        gtfsStopTime.stop.stopID, gtfsStopTime.stopSeq), file = sys.stderr)
                    
                    if problemReport:
                        revisedNodeList = problemReportNodes[gtfsTrips[tripID].shapeEntries[0].shapeID]  
                        if gtfsStopTime.stopSeq not in revisedNodeList:
                            # Make a dummy "error" node for reporting.
                            newShape = gtfs.ShapesEntry(gtfsTrips[tripID].shapeEntries[0].shapeID,
                                gtfsStopTime.stopSeq, gtfsStopTime.stop.gpsLat,gtfsStopTime.stop.gpsLng, False)
                            newPointOnLink = graph.PointOnLink(None, 0)
                            newPointOnLink.pointX = gtfsStopTime.stop.pointX
                            newPointOnLink.pointY = gtfsStopTime.stop.pointY
                            newNode = path_engine.PathEnd(newShape, newPointOnLink)
                            newNode.restart = True
                            revisedNodeList[gtfsStopTime.stopSeq] = newNode
        else:
            print("WARNING: No links for tripID %d." % tripID, file = sys.stderr)

    # Deal with Problem Report:
    if problemReport:
        print("INFO: Output problem report CSV...", file = sys.stderr)
        problemReportNodesOut = {}
        for shapeID in problemReportNodes:
            seqs = problemReportNodes[shapeID].keys()
            seqs.sort()
            ourTgtList = []
            for seq in seqs:
                ourTgtList.append(problemReportNodes[shapeID][seq])
            problemReportNodesOut[shapeID] = ourTgtList                
        problem_report.problemReport(problemReportNodesOut, vistaNetwork)
    
    return ret 

def dumpBusStops(gtfsStops, stopLinkMap, userName, networkName, outFile = sys.stdout):
    """
    dumpBusRouteLinks dumps out a public.bus_route_link.csv file contents.
    @type gtfsStops: dict<int, StopsEntry>
    @type stopLinkMap: dict<int, graph.PointOnLink>
    @type userName: str
    @type networkName: str
    @type outFile: file
    """
    _outHeader("public.bus_stop", userName, networkName, outFile)
    print('"id","link","name","location",', file = outFile)
    
    # Iterate through the stopLinkMap:
    for stopID in stopLinkMap:
        "@type stopID: int"
        pointOnLink = stopLinkMap[stopID]
        "@type pointOnLink: graph.PointOnLink"
        print('"%d","%d","%s","%d"' % (stopID, pointOnLink.link.id, gtfsStops[stopID].stopName, int(pointOnLink.dist)), file = outFile) 

def main(argv):
    global problemReport
    
    # Initialize from command-line parameters:
    if len(argv) < 7:
        syntax()
    dbServer = argv[1]
    networkName = argv[2]
    userName = argv[3]
    password = argv[4]
    shapePath = argv[5]
    pathMatchFilename = argv[6]
    endTime = 86400
    refTime = None
    
    restrictService = set()
    "@type restrictService: set<string>"

    if len(argv) > 6:
        i = 7
        while i < len(argv):
            if argv[i] == "-t" and i < len(argv) - 1:
                refTime = datetime.strptime(argv[i + 1], '%H:%M:%S')
                i += 1
            elif argv[i] == "-e" and i < len(argv) - 1:
                endTime = int(argv[i + 1])
                i += 1
            elif argv[i] == "-c" and i < len(argv) - 1:
                restrictService.add(argv[i + 1])
                i += 1
            elif argv[i] == "-p":
                problemReport = True
            i += 1
    
    if refTime is None:
        print("ERROR: No reference time is specified.")
        syntax(1)
    
    # Default parameters:
    stopSearchRadius = 800
    
    # Restore the stuff that was built with path_match:
    (vistaGraph, gtfsShapes, gtfsNodes, unusedShapeIDs) = restorePathMatch(dbServer, networkName, userName,
        password, shapePath, pathMatchFilename)
    
    # Read in the routes information:
    print("INFO: Read GTFS routesfile...", file = sys.stderr)
    gtfsRoutes = gtfs.fillRoutes(shapePath)
    
    # Read in the stops information:
    print("INFO: Read GTFS stopsfile...", file = sys.stderr)
    gtfsStops = gtfs.fillStops(shapePath, vistaGraph.gps)
    
    # Read in the trips information:
    print("INFO: Read GTFS tripsfile...", file = sys.stderr)
    (gtfsTrips, unusedTripIDs) = gtfs.fillTrips(shapePath, gtfsShapes, gtfsRoutes, unusedShapeIDs, restrictService)
        
    # Read stop times information:
    print("INFO: Read GTFS stop times...", file = sys.stderr)
    gtfsStopTimes = gtfs.fillStopTimes(shapePath, gtfsTrips, gtfsStops, unusedTripIDs)

    # Output the routes file:
    print("INFO: Dumping public.bus_route.csv...", file = sys.stderr)
    with open("public.bus_route.csv", 'w') as outFile:
        dumpBusRoutes(gtfsTrips, userName, networkName, outFile)

    # Output the routes_link file:
    print("INFO: Dumping public.bus_route_link.csv...", file = sys.stderr)
    with open("public.bus_route_link.csv", 'w') as outFile:
        stopLinkMap = dumpBusRouteLinks(gtfsTrips, gtfsNodes, gtfsStopTimes, vistaGraph, stopSearchRadius,
                                        userName, networkName, outFile)
    
    # Then, output the output the stop file:
    print("INFO: Dumping public.bus_stop.csv...", file = sys.stderr)
    with open("public.bus_stop.csv", 'w') as outFile:
        dumpBusStops(gtfsStops, stopLinkMap, userName, networkName, outFile)
        
    print("INFO: Dumping public.bus_frequency.csv...", file = sys.stderr)
    with open("public.bus_frequency.csv", 'w') as outFile:
        _outHeader("public.bus_frequency", userName, networkName, outFile)
        print("\"route\",\"period\",\"frequency\",\"offsettime\",\"preemption\"", file = outFile)
        
        tripIDs = gtfsTrips.keys()
        tripIDs.sort()
        for tripID in tripIDs:
            stopTime = gtfsStopTimes[gtfsTrips[tripID]][0].time
            if stopTime < refTime: # Assume that we're working just within a day.
                stopTime += timedelta(days = int((refTime - stopTime).total_seconds() / 86400) + 1)
            print("%d,1,86400,%d,0" % (tripID, (stopTime - refTime).total_seconds()), file = outFile)

    print("INFO: Dumping public.bus_period.csv...", file = sys.stderr)
    with open("public.bus_period.csv", 'w') as outFile:
        _outHeader("public.bus_period", userName, networkName, outFile)
        print("\"id\",\"starttime\",\"endtime\"", file = outFile)
        print("1,0,%d" % endTime, file = outFile)
        
    print("INFO: Done.", file = sys.stderr)

# Boostrap:
if __name__ == '__main__':
    main(sys.argv)
