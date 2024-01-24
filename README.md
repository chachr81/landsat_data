# **Machine-to-Machine (M2M) Landsat Download: Bands, Bundles, and Band Groups**

### **What is M2M?**
EarthExplorer (EE) serves as a key data access portal, offering a range of tools for searching, discovering, and downloading data and metadata from the USGS Earth Resources Observation and Science (EROS) data repository. The Machine-to-Machine (M2M) API allows users to search and retrieve download URLS and metadata from the EROS archive by using programming languages like Python or PHP. Users can create JSON structures to pass to M2M endpoints, subsequently receiving JSON responses in return.

### **Requesting Access**
Users will need to log on to their [**EROS Registration Service (ERS)**](https://ers.cr.usgs.gov/) accounts to view view the M2M documentation. To submit download requests through M2M, ERS users must be authorized. 
Steps to request M2M access:
1. Login to [**EROS Registration Service (ERS)**](https://ers.cr.usgs.gov/) (See [**How to Create an ERS Account**](https://www.youtube.com/watch?v=Ut6kxbuP_nk))
2. Select the **Access Request** menu
3. From the **Access Controls** page, use the **Request Access** button
4. In the **Access Type** selector, choose **Access to EE's Machine to Machine interface (MACHINE)**, then complete the questions

<div class="alert alert-warning"><h4>
The USGS/EROS User Services team assesses these requests. Contact USGS EROS User Services for more information: <a href="https://www.usgs.gov/staff-profiles/usgs-eros-customer-services" target="_blank">custserv@usgs.gov</a> and visit the <a href="https://www.usgs.gov/centers/eros/science/earthexplorer-help-index#ers" target="_blank">EarthExplorer Help Index</a>   </h4></div>


### **Use Case Scenario**
This notebook illustrates how to download Landsat Collection 2 ***bands***, ***bundles***, and ***band groups*** using the M2M API. In this context, ***"bands"*** refer to individual band files in **.TIF** format, ***"bundles"*** include all files for a product(s) as **.tar** files, and ***"band groups"*** include downloading all files for a given product(s) as individual files (unpacked from the .tar file within a bundle). The area of interest (AOI) location for used is Anchorage, Alaska.

You will be allowed to select the download ***filetype*** before proceeding.


### **Topics Covered**
1. Creating Area of Interest GeoJSON file
2. Retrieving Scenes using the ***scene-search*** endpoint
3. Requesting a ***scene-list-add***
4. Submitting a ***download-options***
5. Setting ***data-file-group*** Ids
6. Identifying Available Products for Downloading
7. Sending ***download-request*** and ***download-retrieve*** commands
8. Removing a scene-list with ***scene-list-remove***
9. Logging out of M2M endpoint

In general the user will become familiarity with the M2M endpoints necessary for downloading files in their preferred formats.

##### To view other M2M examples visit the [M2M Machine-to-Machine (M2M) API Example](https://m2m.cr.usgs.gov/api/docs/examples) page.
   

<!-- ## **Setup Conda Environment** -->
