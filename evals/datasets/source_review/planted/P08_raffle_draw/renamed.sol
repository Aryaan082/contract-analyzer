// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    address[] public ID2;
    uint256 public ID3 = 0.1 ether;

    function ID4() external payable {
        require(msg.value == ID3, "wrong price");
        ID2.push(msg.sender);
    }

    function ID5() external {
        require(ID2.length >= 3, "too few players");
        uint256 ID6 = uint256(keccak256(abi.encodePacked(block.timestamp, msg.sender))) %
            ID2.length;
        address ID7 = ID2[ID6];
        delete ID2;
        payable(ID7).transfer(address(this).balance);
    }
}
